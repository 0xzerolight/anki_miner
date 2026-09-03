"""A close that never finishes has to be readable from the log alone.

The 10 h zombie -- a hidden process still alive long after the user closed the
window -- left one count-only line behind: how many threads were still running
at the grace boundary, and nothing about which ones, how long each join took,
or whether the deferred poll was still waiting an hour later. These tests pin
the five anchors that answer that: ``Close requested`` (the close began),
``Close join`` (per live worker, joined or timed out), ``Deferring close``
(which workers pushed the close onto the poll), ``Close still waiting`` (the
poll is alive and these are the laggards) and ``Close finalized`` (the app is
about to quit). A ``Close finalized`` with no later ``Session end`` localises a
hang to the event loop instead of the workers.
"""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, QThread

_WINDOW_LOGGER = "anki_miner.gui.main_window"
_TASKS_LOGGER = "anki_miner.gui.controllers.background_tasks"


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    """A real MainWindow with the side-effect-heavy startup stubbed out."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


@pytest.fixture
def quit_calls(monkeypatch, caplog):
    """Record ``QApplication.quit()`` and the log messages seen at that moment."""
    from anki_miner.gui.controllers import background_tasks as bg_module

    calls: list[list[str]] = []

    class _QuitRecorder:
        @staticmethod
        def quit() -> None:
            calls.append([record.getMessage() for record in caplog.records])

    monkeypatch.setattr(bg_module, "QApplication", _QuitRecorder)
    return calls


@pytest.fixture
def shutdown_log(caplog):
    """Capture both shutdown loggers at DEBUG, restoring their levels."""
    with (
        caplog.at_level(logging.DEBUG, logger=_WINDOW_LOGGER),
        caplog.at_level(logging.DEBUG, logger=_TASKS_LOGGER),
    ):
        yield caplog


class _FakeWorker:
    """A worker handle whose bounded join times out until :meth:`finish`."""

    def __init__(self, *, running: bool = True, wait_result: bool = False) -> None:
        self._running = running
        self._wait_result = wait_result
        self.cancel_called = False

    def isRunning(self) -> bool:  # noqa: N802 (Qt convention)
        return self._running

    def cancel(self) -> None:
        self.cancel_called = True

    def wait(self, timeout_ms: int) -> bool:
        if self._wait_result:
            self._running = False
            return True
        return False

    def finish(self) -> None:
        self._running = False


class _SleepWorker(QThread):
    """A real QThread that outlives the close grace and ignores ``cancel``.

    No ``cancel()`` hook on purpose: the join policy must time out against a
    thread that does not cooperate, which is the shape the zombie had. The test
    releases it explicitly once the assertions are made.
    """

    def __init__(self) -> None:
        super().__init__()
        self._release = threading.Event()

    def run(self) -> None:
        self._release.wait(5.0)

    def release(self) -> None:
        self._release.set()


def _trigger_close(window) -> MagicMock:
    """Dispatch a close event and return the fake event so callers can assert."""
    event = MagicMock(spec=QEvent)
    window.closeEvent(event)
    return event


def _messages(caplog, anchor: str) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith(anchor)]


def _one(caplog, anchor: str) -> str:
    found = _messages(caplog, anchor)
    assert len(found) == 1, f"expected exactly one {anchor!r} line, got {found}"
    return found[0]


def _levels(caplog, anchor: str) -> list[int]:
    return [record.levelno for record in caplog.records if record.getMessage().startswith(anchor)]


class TestCloseRequested:
    """The close request itself is logged, before anything can hide the window."""

    def test_close_requested_names_the_tab_count(self, main_window, shutdown_log):
        _trigger_close(main_window)

        message = _one(shutdown_log, "Close requested:")
        assert "tabs=" in message
        assert f"tabs={main_window.tabs.count()}" in message
        assert logging.INFO in _levels(shutdown_log, "Close requested:")


class TestImmediateClose:
    """An idle close finalizes on the spot and says so before accepting."""

    def test_close_finalized_before_accept(self, main_window, shutdown_log):
        event = _trigger_close(main_window)

        message = _one(shutdown_log, "Close finalized:")
        assert "deferred=no" in message
        assert "waited_s=" in message
        assert "laggards=" in message
        event.accept.assert_called_once()

    def test_idle_handles_log_no_join_line(self, main_window, shutdown_log):
        """``Close join`` is per LIVE worker: the dozen ``None`` handles stay quiet."""
        _trigger_close(main_window)

        assert _messages(shutdown_log, "Close join:") == []


class TestJoinAndDefer:
    """A worker that outlives the grace names itself in both anchors."""

    def test_sleeping_worker_logs_timeout_and_defers(self, main_window, shutdown_log):
        worker = _SleepWorker()
        worker.start()
        try:
            main_window.background_tasks.update_worker = worker

            event = _trigger_close(main_window)

            join_line = _one(shutdown_log, "Close join:")
            assert "worker=_SleepWorker" in join_line
            assert "outcome=timeout" in join_line
            assert "elapsed_ms=" in join_line
            assert logging.INFO in _levels(shutdown_log, "Close join:")

            defer_line = _one(shutdown_log, "Deferring close:")
            assert "count=1" in defer_line
            assert "grace_ms=2000" in defer_line
            assert "workers=_SleepWorker" in defer_line
            assert _levels(shutdown_log, "Deferring close:") == [logging.WARNING]
            event.ignore.assert_called_once()
        finally:
            worker.release()
            worker.wait(6000)

    def test_joined_worker_reports_the_join_outcome(self, main_window, shutdown_log):
        main_window.background_tasks.update_worker = _FakeWorker(wait_result=True)

        _trigger_close(main_window)

        join_line = _one(shutdown_log, "Close join:")
        assert "worker=_FakeWorker" in join_line
        assert "outcome=joined" in join_line
        assert _messages(shutdown_log, "Deferring close:") == []


class TestDeferredPoll:
    """The deferred poll proves it is still alive, on a bounded cadence."""

    def test_still_waiting_logged_once_per_interval(self, main_window, shutdown_log):
        from anki_miner.gui.controllers import background_tasks as bg_module

        worker = _FakeWorker()
        main_window.background_tasks.update_worker = worker
        _trigger_close(main_window)

        polls = int(bg_module._CLOSE_POLL_LOG_INTERVAL_S * 1000 / bg_module._CLOSE_POLL_INTERVAL_MS)
        for _ in range(polls - 1):
            main_window.background_tasks._poll_deferred_close()
        assert _messages(shutdown_log, "Close still waiting:") == []

        main_window.background_tasks._poll_deferred_close()

        message = _one(shutdown_log, "Close still waiting:")
        assert "workers=_FakeWorker" in message
        assert "waited_s=" in message
        assert _levels(shutdown_log, "Close still waiting:") == [logging.WARNING]

        worker.finish()

    def test_deferred_close_finalizes_before_quit(self, main_window, shutdown_log, quit_calls):
        worker = _FakeWorker()
        main_window.background_tasks.update_worker = worker
        _trigger_close(main_window)
        assert _messages(shutdown_log, "Close finalized:") == []

        worker.finish()
        main_window.background_tasks._poll_deferred_close()

        message = _one(shutdown_log, "Close finalized:")
        assert "deferred=yes" in message
        assert "waited_s=" in message
        assert "laggards=_FakeWorker" in message
        assert len(quit_calls) == 1
        assert any(
            seen.startswith("Close finalized:") for seen in quit_calls[0]
        ), "Close finalized must be logged BEFORE QApplication.quit()"

    def test_deleted_laggard_is_labelled_not_fatal(self, main_window, shutdown_log, quit_calls):
        """A laggard sip-deleted between two polls must not break the trace."""
        from PyQt6 import sip

        worker = QThread()
        sip.delete(worker)
        main_window.background_tasks._close_laggards = [worker]
        main_window.background_tasks._close_started_at = None

        main_window.background_tasks._poll_deferred_close()

        assert "laggards=<deleted>" in _one(shutdown_log, "Close finalized:")
        assert len(quit_calls) == 1


class TestDeferredReleaseFailure:
    """The release swallowed on the deferred path leaves a diagnostic behind."""

    def test_release_failure_is_recorded(self, main_window, shutdown_log, quit_calls, monkeypatch):
        def _boom() -> bool:
            raise RuntimeError("sqlite handle busy")

        monkeypatch.setattr(main_window, "release_dictionary_resources", _boom)
        worker = _FakeWorker()
        main_window.background_tasks.update_worker = worker
        _trigger_close(main_window)

        worker.finish()
        main_window.background_tasks._poll_deferred_close()

        message = _one(shutdown_log, "Ignored failure during dictionary resource release")
        assert "RuntimeError" in message
        assert "sqlite handle busy" in message
        assert len(quit_calls) == 1

        # qtbot closes the window again at teardown, which would re-enter the
        # raising stub outside the test's own guard.
        monkeypatch.undo()


class TestWatchdogStillStopped:
    """The stall watchdog stop is not a casualty of the new tracing."""

    def test_watchdog_stopped_on_close(self, main_window):
        stops: list[bool] = []
        main_window._stall_watchdog = SimpleNamespace(stop=lambda: stops.append(True))

        _trigger_close(main_window)

        assert stops == [True]

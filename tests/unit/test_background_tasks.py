"""Tests pinning BackgroundTaskController._release_worker (OVH-065).

_release_worker is called via the `finished` signal connection wired in
start_validation() and check_for_updates().  Three behaviours are pinned:

  1. A run is refused while isRunning() → returns False / no second worker.
  2. Emitting `finished` triggers worker.deleteLater() and nulls the handle.
  3. The handle is NOT nulled when a second run already replaced the worker
     (emit worker A's `finished` after worker B is installed → B survives).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

# ---------------------------------------------------------------------------
# Fake worker
# ---------------------------------------------------------------------------


class _FakeWorker(QObject):
    """Lightweight QObject stand-in for a CancellableWorker / UpdateWorkerThread.

    Carries a real ``finished`` pyqtSignal so the lambda wired in
    start_validation() / check_for_updates() fires when we call
    ``emit_finished()``.  ``deleteLater`` is replaced by a MagicMock so we can
    assert it was called without scheduling actual Qt deferred deletion.
    """

    finished = pyqtSignal()

    def __init__(self, *, running: bool = False) -> None:
        super().__init__()
        self._running = running
        self.deleteLater = MagicMock()  # type: ignore[method-assign]
        # Minimal stubs expected by start_validation / check_for_updates.
        self.result_ready = pyqtSignal(object)
        self.error = pyqtSignal(str)

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_finished(self) -> None:
        """Simulate the thread's finished signal firing after it exits."""
        self._running = False
        self.finished.emit()


# ---------------------------------------------------------------------------
# Fixture: a BackgroundTaskController with its heavy collaborators patched out
# ---------------------------------------------------------------------------


@pytest.fixture
def controller(qtbot):
    """BackgroundTaskController with the window dependency stubbed out.

    BackgroundTaskController.__init__ calls super().__init__(window), so the
    parent must be a real QObject.  We use a QWidget placeholder; no real
    MainWindow is constructed.
    """
    from PyQt6.QtWidgets import QWidget

    from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

    # A bare QWidget is a valid QObject parent and avoids all of MainWindow's
    # heavy startup (config loading, validation, AnkiConnect probing, etc.).
    parent_widget = QWidget()
    qtbot.addWidget(parent_widget)

    ctrl = BackgroundTaskController(parent_widget)  # type: ignore[arg-type]
    return ctrl


# ---------------------------------------------------------------------------
# Helper: patch start_validation to inject a _FakeWorker
# ---------------------------------------------------------------------------


def _inject_fake_validation_worker(controller, worker: _FakeWorker) -> bool:
    """Wire *worker* into the controller the same way start_validation() does.

    This replaces the ValidationWorkerThread construction without touching any
    real Anki/ffmpeg services.
    """
    if controller.validation_worker is not None and controller.validation_worker.isRunning():
        return False
    controller.validation_worker = worker
    worker.finished.connect(lambda w=worker: controller._release_worker("validation_worker", w))
    worker.start()
    return True


def _inject_fake_update_worker(controller, worker: _FakeWorker) -> bool:
    """Wire *worker* into the controller the same way check_for_updates() does."""
    if controller.update_worker is not None and controller.update_worker.isRunning():
        return False
    controller.update_worker = worker
    worker.finished.connect(lambda w=worker: controller._release_worker("update_worker", w))
    worker.start()
    return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReleaseWorkerRefusesSecondRun:
    """While isRunning(), a second start attempt must be rejected."""

    def test_validation_refused_while_running(self, controller):
        worker_a = _FakeWorker()
        started = _inject_fake_validation_worker(controller, worker_a)
        assert started is True
        assert worker_a.isRunning()

        worker_b = _FakeWorker()
        started_again = _inject_fake_validation_worker(controller, worker_b)

        assert started_again is False
        # The handle still points at worker_a, not worker_b.
        assert controller.validation_worker is worker_a

    def test_update_refused_while_running(self, controller):
        worker_a = _FakeWorker()
        _inject_fake_update_worker(controller, worker_a)
        assert worker_a.isRunning()

        worker_b = _FakeWorker()
        result = _inject_fake_update_worker(controller, worker_b)

        assert result is False
        assert controller.update_worker is worker_a


class TestReleaseWorkerNullsHandle:
    """Emitting ``finished`` must call deleteLater() and null the handle."""

    def test_validation_handle_nulled_after_finished(self, controller, qtbot):
        worker = _FakeWorker()
        _inject_fake_validation_worker(controller, worker)

        worker.emit_finished()

        assert controller.validation_worker is None
        worker.deleteLater.assert_called_once()

    def test_update_handle_nulled_after_finished(self, controller, qtbot):
        worker = _FakeWorker()
        _inject_fake_update_worker(controller, worker)

        worker.emit_finished()

        assert controller.update_worker is None
        worker.deleteLater.assert_called_once()


class TestReleaseWorkerPreservesReplacedHandle:
    """If a second run replaced the handle before finished fires, preserve it.

    Scenario:
      1. Worker A is installed → handle = A.
      2. Worker A finishes and returns False (simulating worker started then
         stopped before B ran).  Worker B is installed → handle = B.
      3. Worker A's finished signal fires (delayed / out of order).
      → handle must still be B; B must not receive deleteLater.
    """

    def test_handle_not_nulled_when_already_replaced(self, controller, qtbot):
        worker_a = _FakeWorker()
        _inject_fake_validation_worker(controller, worker_a)

        # Mark A as no longer running so B can be installed, but DON'T fire
        # A's finished signal yet — simulate the race window.
        worker_a._running = False

        worker_b = _FakeWorker()
        _inject_fake_validation_worker(controller, worker_b)
        assert controller.validation_worker is worker_b

        # Now fire A's finished — _release_worker sees attr != worker so it
        # must NOT null the handle (which now points at B).
        worker_a.finished.emit()

        assert controller.validation_worker is worker_b, "handle was cleared by stale worker_a finished signal"
        worker_b.deleteLater.assert_not_called()
        # A still gets its deleteLater (cleanup is unconditional).
        worker_a.deleteLater.assert_called_once()


class _FakeYtdlpWorker(QObject):
    """Fake yt-dlp worker with real, connectable result_ready/error/finished signals."""

    finished = pyqtSignal()
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self.deleteLater = MagicMock()  # type: ignore[method-assign]

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_finished(self) -> None:
        self._running = False
        self.finished.emit()


class TestStartYtdlpUpdate:
    """start_ytdlp_update mirrors check_for_updates: guard, wire, start."""

    def _patch(self, monkeypatch, worker, captured=None):
        def _make_worker(updater, *, force, parent=None):
            if captured is not None:
                captured["force"] = force
                captured["updater"] = updater
            return worker

        monkeypatch.setattr("anki_miner.gui.workers.ytdlp_update_worker.YtdlpUpdateWorker", _make_worker)
        monkeypatch.setattr("anki_miner.services.ytdlp_updater.YtdlpUpdater", lambda config: MagicMock(name="updater"))

    def test_starts_and_forwards_result(self, controller, qtbot, monkeypatch):
        from anki_miner.config import AnkiMinerConfig

        worker = _FakeYtdlpWorker()
        captured: dict = {}
        self._patch(monkeypatch, worker, captured)

        forwarded: list = []
        controller.ytdlp_update_result.connect(forwarded.append)

        controller.start_ytdlp_update(AnkiMinerConfig(), force=True)

        assert captured["force"] is True
        assert controller.ytdlp_update_worker is worker

        sentinel = object()
        worker.result_ready.emit(sentinel)
        assert forwarded == [sentinel]

    def test_refused_while_running(self, controller, qtbot, monkeypatch):
        from anki_miner.config import AnkiMinerConfig

        worker_a = _FakeYtdlpWorker()
        self._patch(monkeypatch, worker_a)

        controller.start_ytdlp_update(AnkiMinerConfig(), force=False)
        assert controller.ytdlp_update_worker is worker_a

        # A second start while running must not replace the handle.
        controller.start_ytdlp_update(AnkiMinerConfig(), force=False)
        assert controller.ytdlp_update_worker is worker_a

    def test_handle_nulled_after_finished(self, controller, qtbot, monkeypatch):
        from anki_miner.config import AnkiMinerConfig

        worker = _FakeYtdlpWorker()
        self._patch(monkeypatch, worker)

        controller.start_ytdlp_update(AnkiMinerConfig(), force=False)
        worker.emit_finished()

        assert controller.ytdlp_update_worker is None
        worker.deleteLater.assert_called_once()

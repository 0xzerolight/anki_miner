"""Tests for :class:`MainWindow.closeEvent` tab worker teardown logic.

Constructing a real ``MainWindow`` kicks off a validation worker and persists
config to disk, which we want to avoid in unit tests. These tests therefore
patch the heavy collaborators (config manager + validation service) and insert
fake subclasses of the real tab widgets into ``window.tabs`` before triggering
``closeEvent`` directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig):
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    # Prevent any validation worker from actually running.
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)


@pytest.fixture
def main_window(monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.deleteLater()


class _FakeWorker:
    """Minimal stand-in for a ``CancellableWorker`` thread.

    ``wait_result=False`` simulates a thread that outlives the grace join:
    ``wait()`` times out (returns False) and the worker keeps running until
    the test calls :meth:`finish`.
    """

    def __init__(self, *, running: bool = False, wait_result: bool = True) -> None:
        self._running = running
        self._wait_result = wait_result
        self.cancel_called = False
        self.wait_called = False
        self.wait_called_with: int | None = None

    def isRunning(self) -> bool:  # noqa: N802 (Qt convention)
        return self._running

    def cancel(self) -> None:
        self.cancel_called = True

    def wait(self, timeout_ms: int) -> bool:
        self.wait_called = True
        self.wait_called_with = timeout_ms
        if self._wait_result:
            self._running = False
            return True
        return False

    def finish(self) -> None:
        """Simulate the thread eventually exiting on its own."""
        self._running = False


class _FakePrewarmWorker:
    """Stand-in for the cache prewarm worker: a QThread with NO cancel hook.

    Deliberately has no ``cancel`` attribute so the join policy is exercised
    against the real prewarm worker's shape (closeEvent must not assume every
    worker is cancellable).
    """

    def __init__(self, *, running: bool = False) -> None:
        self._running = running
        self.wait_called = False
        self.wait_args: tuple | None = None

    def isRunning(self) -> bool:  # noqa: N802 (Qt convention)
        return self._running

    def wait(self, *args) -> bool:
        self.wait_called = True
        self.wait_args = args
        self._running = False
        return True


class _FakeYouTubeTab(YouTubeTab):
    """Real YouTubeTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        # Bypass YouTubeTab.__init__ — we only need attribute storage and the
        # two methods that closeEvent interacts with.
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread: _FakeWorker | None = _FakeWorker(running=worker_running)
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeEpisodeTab(SingleEpisodeTab):
    """Real SingleEpisodeTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False, wait_result: bool = True) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread = _FakeWorker(running=worker_running, wait_result=wait_result)


class _FakeBatchTab(BatchProcessingTab):
    """Real BatchProcessingTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread = _FakeWorker(running=worker_running)


class _FakeDeckBuilderTab(DeckBuilderTab):
    """Real DeckBuilderTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread: _FakeWorker | None = _FakeWorker(running=worker_running)


class _FakeSettingsTab(SettingsTab):
    """Real SettingsTab subclass that skips the heavy ``__init__`` (T-12).

    SettingsTab's three short-lived AnkiConnect workers (fetch fields, fetch
    decks, apply/remove styling) live on :class:`AnkiProbeController` (T-66)
    with no ``worker_thread`` attribute, so closeEvent must discover them via
    ``iter_close_workers`` (tab → controller delegation, exercised for real
    here) and route each through the same join policy as the mining tabs.
    """

    def __init__(self, *, fields_running=False, decks_running=False, styling_running=False, wait_result=True) -> None:
        from PyQt6.QtWidgets import QWidget

        from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController

        QWidget.__init__(self)
        self._anki_probe = AnkiProbeController(
            parent=self,
            anki_panel=MagicMock(),
            filtering_panel=MagicMock(),
            get_config=MagicMock(),
        )
        self._anki_probe._fetch_fields_worker = _FakeWorker(running=fields_running, wait_result=wait_result)
        self._anki_probe._fetch_decks_worker = _FakeWorker(running=decks_running, wait_result=wait_result)
        self._anki_probe._styling_worker = _FakeWorker(running=styling_running, wait_result=wait_result)


def _trigger_close(window) -> MagicMock:
    """Dispatch a close event and return the fake event so callers can assert."""
    event = MagicMock(spec=QEvent)
    window.closeEvent(event)
    return event


class TestCloseEventYouTubeTab:
    """closeEvent should cancel a running YouTube worker and call shutdown()."""

    def test_running_youtube_worker_is_cancelled(self, main_window):
        yt_tab = _FakeYouTubeTab(worker_running=True)
        main_window.tabs.addTab(yt_tab, "YouTube")

        event = _trigger_close(main_window)

        assert yt_tab.worker_thread.cancel_called
        assert yt_tab.worker_thread.wait_called_with == 2000
        assert yt_tab.shutdown_called
        event.accept.assert_called_once()

    def test_idle_youtube_tab_still_calls_shutdown(self, main_window):
        yt_tab = _FakeYouTubeTab(worker_running=False)
        main_window.tabs.addTab(yt_tab, "YouTube")

        _trigger_close(main_window)

        assert not yt_tab.worker_thread.cancel_called
        # shutdown() runs regardless so the probe worker is torn down.
        assert yt_tab.shutdown_called


class TestCloseEventOtherTabs:
    """Single episode and batch tabs should still have their workers cancelled."""

    def test_running_single_episode_worker_cancelled(self, main_window):
        tab = _FakeEpisodeTab(worker_running=True)
        main_window.tabs.addTab(tab, "Episode")

        _trigger_close(main_window)

        assert tab.worker_thread.cancel_called
        assert tab.worker_thread.wait_called_with == 2000

    def test_running_batch_worker_cancelled(self, main_window):
        tab = _FakeBatchTab(worker_running=True)
        main_window.tabs.addTab(tab, "Batch")

        _trigger_close(main_window)

        assert tab.worker_thread.cancel_called
        assert tab.worker_thread.wait_called_with == 2000


class TestCloseEventDeckBuilderTab:
    """Deck builder worker (held on ``worker_thread``) must also be torn down."""

    def test_running_deck_builder_worker_cancelled(self, main_window):
        tab = _FakeDeckBuilderTab(worker_running=True)
        main_window.tabs.addTab(tab, "Deck Builder")

        _trigger_close(main_window)

        # cancel() also opens the confirm gate, so a worker blocked awaiting
        # Build unblocks and exits cleanly before the window closes.
        assert tab.worker_thread.cancel_called
        assert tab.worker_thread.wait_called_with == 2000

    def test_idle_deck_builder_worker_not_cancelled(self, main_window):
        tab = _FakeDeckBuilderTab(worker_running=False)
        main_window.tabs.addTab(tab, "Deck Builder")

        _trigger_close(main_window)

        assert not tab.worker_thread.cancel_called


class TestCloseEventSettingsTab:
    """SettingsTab's AnkiConnect workers (T-12) must be cancelled + joined."""

    def test_running_settings_workers_cancelled_and_joined(self, main_window):
        tab = _FakeSettingsTab(fields_running=True, decks_running=True, styling_running=True)
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        for worker in (
            tab._anki_probe._fetch_fields_worker,
            tab._anki_probe._fetch_decks_worker,
            tab._anki_probe._styling_worker,
        ):
            assert worker.cancel_called
            assert worker.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_idle_settings_workers_not_cancelled(self, main_window):
        tab = _FakeSettingsTab()
        main_window.tabs.addTab(tab, "Settings")

        _trigger_close(main_window)

        for worker in (
            tab._anki_probe._fetch_fields_worker,
            tab._anki_probe._fetch_decks_worker,
            tab._anki_probe._styling_worker,
        ):
            assert not worker.cancel_called

    def test_settings_laggard_defers_close(self, main_window):
        tab = _FakeSettingsTab(styling_running=True, wait_result=False)
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert tab._anki_probe._styling_worker.cancel_called
        event.accept.assert_not_called()
        event.ignore.assert_called_once()


class TestCloseEventNoActiveWorkers:
    """Empty tab list should close cleanly without touching any worker."""

    def test_close_with_no_tabs(self, main_window):
        event = _trigger_close(main_window)
        event.accept.assert_called_once()


_WINDOW_OWNED_WORKER_ATTRS = ["validation_worker", "update_worker", "_jmdict_migration_worker"]


@pytest.fixture
def quit_calls(monkeypatch):
    """Record QApplication.quit() calls made by the deferred-close machinery."""
    from anki_miner.gui import main_window as mw_module

    calls: list[bool] = []

    class _QuitRecorder:
        @staticmethod
        def quit() -> None:
            calls.append(True)

    monkeypatch.setattr(mw_module, "QApplication", _QuitRecorder)
    return calls


class TestCloseEventWindowOwnedWorkers:
    """closeEvent must join all four window-owned workers before accepting."""

    @pytest.mark.parametrize("attr", _WINDOW_OWNED_WORKER_ATTRS)
    def test_running_worker_cancelled_and_joined(self, main_window, attr):
        worker = _FakeWorker(running=True)
        setattr(main_window, attr, worker)

        event = _trigger_close(main_window)

        assert worker.cancel_called
        assert worker.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_prewarm_worker_joined_without_timeout(self, main_window):
        worker = _FakePrewarmWorker(running=True)
        main_window._prewarm_worker = worker

        event = _trigger_close(main_window)

        # The prewarm worker has no cancel hook; it must be joined with an
        # unbounded wait() — a bounded wait could expire and abandon it.
        assert worker.wait_called
        assert worker.wait_args == ()
        event.accept.assert_called_once()


class TestCloseEventJoinTimeoutPolicy:
    """``wait()`` returning False must never accept the close with a live thread.

    Policy under test: a worker that outlives the grace join defers the close
    — the window hides (so closing feels instant), the event is ignored (so
    Qt does not destroy the window and its running QThread children), and a
    poll timer quits the application only once every laggard has exited.
    """

    @pytest.mark.parametrize("attr", _WINDOW_OWNED_WORKER_ATTRS)
    def test_window_owned_laggard_defers_close(self, main_window, attr):
        main_window.show()
        worker = _FakeWorker(running=True, wait_result=False)
        setattr(main_window, attr, worker)

        event = _trigger_close(main_window)

        assert worker.cancel_called  # cancel is still requested
        event.accept.assert_not_called()
        event.ignore.assert_called_once()
        assert main_window.isHidden()
        assert main_window._close_poll_timer is not None
        assert main_window._close_poll_timer.isActive()

    def test_tab_laggard_defers_close(self, main_window):
        tab = _FakeEpisodeTab(worker_running=True, wait_result=False)
        main_window.tabs.addTab(tab, "Episode")

        event = _trigger_close(main_window)

        assert tab.worker_thread.cancel_called
        event.accept.assert_not_called()
        event.ignore.assert_called_once()

    def test_poll_keeps_app_alive_while_laggard_runs(self, quit_calls, main_window):
        worker = _FakeWorker(running=True, wait_result=False)
        main_window.update_worker = worker
        _trigger_close(main_window)

        main_window._poll_deferred_close()

        assert quit_calls == []
        assert main_window._close_poll_timer.isActive()

    def test_poll_quits_only_after_laggards_exit(self, quit_calls, main_window):
        worker = _FakeWorker(running=True, wait_result=False)
        main_window.update_worker = worker
        _trigger_close(main_window)

        worker.finish()
        main_window._poll_deferred_close()

        assert quit_calls == [True]
        assert not main_window._close_poll_timer.isActive()

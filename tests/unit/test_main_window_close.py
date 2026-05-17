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
    """Minimal stand-in for a ``CancellableWorker`` thread."""

    def __init__(self, *, running: bool = False) -> None:
        self._running = running
        self.cancel_called = False
        self.wait_called_with: int | None = None

    def isRunning(self) -> bool:  # noqa: N802 (Qt convention)
        return self._running

    def cancel(self) -> None:
        self.cancel_called = True
        self._running = False

    def wait(self, timeout_ms: int) -> bool:
        self.wait_called_with = timeout_ms
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

    def __init__(self, *, worker_running: bool = False) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread = _FakeWorker(running=worker_running)


class _FakeBatchTab(BatchProcessingTab):
    """Real BatchProcessingTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread = _FakeWorker(running=worker_running)


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


class TestCloseEventNoActiveWorkers:
    """Empty tab list should close cleanly without touching any worker."""

    def test_close_with_no_tabs(self, main_window):
        event = _trigger_close(main_window)
        event.accept.assert_called_once()

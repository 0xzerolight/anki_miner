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

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab


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
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def main_window(qtbot, monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
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
        # SingleEpisodeTab.release_dictionary_resources checks this attribute.
        self.curation_processor = None

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
        self._processor = None  # needed by inherited release_dictionary_resources

    def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeAudiobookTab(AudiobookTab):
    """Real AudiobookTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        # Bypass AudiobookTab.__init__ — we only need attribute storage and the
        # two methods that closeEvent interacts with.
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread: _FakeWorker | None = _FakeWorker(running=worker_running)
        self.shutdown_called = False
        self._processor = None  # needed by inherited release_dictionary_resources

    def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeEpisodeTab(SingleEpisodeTab):
    """Real SingleEpisodeTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False, wait_result: bool = True) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread = _FakeWorker(running=worker_running, wait_result=wait_result)
        self._processor = None  # needed by inherited release_dictionary_resources


class _FakeBatchTab(BatchProcessingTab):
    """Real BatchProcessingTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread = _FakeWorker(running=worker_running)
        self._processor = None  # needed by inherited release_dictionary_resources


class _FakeDeckBuilderTab(DeckBuilderTab):
    """Real DeckBuilderTab subclass that skips the heavy ``__init__``."""

    def __init__(self, *, worker_running: bool = False) -> None:
        from PyQt6.QtWidgets import QWidget

        QWidget.__init__(self)
        self.worker_thread: _FakeWorker | None = _FakeWorker(running=worker_running)
        self._processor = None  # needed by inherited release_dictionary_resources


class _FakeSettingsTab(SettingsTab):
    """Real SettingsTab subclass that skips the heavy ``__init__`` (T-12).

    SettingsTab's three short-lived AnkiConnect workers (fetch fields, fetch
    decks, apply/remove styling) live on :class:`AnkiProbeController` (T-66)
    with no ``worker_thread`` attribute, so closeEvent must discover them via
    ``iter_close_workers`` (tab → controller delegation, exercised for real
    here) and route each through the same join policy as the mining tabs.
    """

    def __init__(self, *, fields_running=False, decks_running=False, wait_result=True) -> None:
        from unittest.mock import MagicMock

        from PyQt6.QtWidgets import QWidget

        from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
        from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow
        from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
        from anki_miner.gui.controllers.frequency_import_flow import FrequencyImportFlow
        from anki_miner.gui.controllers.zip_import_flow import ZipImportFlow

        QWidget.__init__(self)
        self._anki_probe = AnkiProbeController(
            parent=self,
            anki_panel=MagicMock(),
            filtering_panel=MagicMock(),
            get_config=MagicMock(),
        )
        self._anki_probe._fetch_fields_worker = _FakeWorker(running=fields_running, wait_result=wait_result)
        self._anki_probe._fetch_decks_worker = _FakeWorker(running=decks_running, wait_result=wait_result)
        # Import-flow controllers with idle (None) workers (OVH-004, 059, 060).
        self._dict_import_flow = DictionaryImportFlow(
            parent=self,
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
            notify_config_changed=MagicMock(),
        )
        self._audio_pack_import_flow = AudioPackImportFlow(
            parent=self,
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
        )
        self._frequency_import_flow = FrequencyImportFlow(
            parent=self,
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
        )
        self._zip_import_flow = ZipImportFlow(self)
        # Real SettingsTab shape: shutdown()/flush_pending_settings touch the
        # auto-save debounce timer, so the fake needs one too (idle).
        from PyQt6.QtCore import QTimer

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)


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


class TestCloseEventAudiobookTab:
    """closeEvent should cancel a running Audiobook worker and call shutdown()."""

    def test_running_audiobook_worker_is_cancelled(self, main_window):
        ab_tab = _FakeAudiobookTab(worker_running=True)
        main_window.tabs.addTab(ab_tab, "Audiobook")

        event = _trigger_close(main_window)

        assert ab_tab.worker_thread.cancel_called
        assert ab_tab.worker_thread.wait_called_with == 2000
        assert ab_tab.shutdown_called
        event.accept.assert_called_once()

    def test_idle_audiobook_tab_still_calls_shutdown(self, main_window):
        ab_tab = _FakeAudiobookTab(worker_running=False)
        main_window.tabs.addTab(ab_tab, "Audiobook")

        _trigger_close(main_window)

        assert not ab_tab.worker_thread.cancel_called
        # shutdown() runs regardless so the curation gate is poisoned.
        assert ab_tab.shutdown_called


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
        tab = _FakeSettingsTab(fields_running=True, decks_running=True)
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        for worker in (
            tab._anki_probe._fetch_fields_worker,
            tab._anki_probe._fetch_decks_worker,
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
        ):
            assert not worker.cancel_called

    def test_settings_laggard_defers_close(self, main_window):
        tab = _FakeSettingsTab(fields_running=True, wait_result=False)
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert tab._anki_probe._fetch_fields_worker.cancel_called
        event.accept.assert_not_called()
        event.ignore.assert_called_once()


class TestCloseEventSettingsTabImportFlowWorkers:
    """Import-flow workers (OVH-004, 059, 060) must be cancelled + joined at closeEvent."""

    def test_running_dict_import_worker_cancelled(self, main_window):
        tab = _FakeSettingsTab()
        w = _FakeWorker(running=True)
        tab._dict_import_flow._active_import_worker = w
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert w.cancel_called
        assert w.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_running_audio_pack_import_worker_cancelled(self, main_window):
        tab = _FakeSettingsTab()
        w = _FakeWorker(running=True)
        tab._audio_pack_import_flow._active_import_worker = w
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert w.cancel_called
        assert w.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_running_frequency_import_worker_cancelled(self, main_window):
        tab = _FakeSettingsTab()
        w = _FakeWorker(running=True)
        tab._frequency_import_flow._active_import_worker = w
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert w.cancel_called
        assert w.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_running_zip_pitch_worker_cancelled(self, main_window):
        tab = _FakeSettingsTab()
        w = _FakeWorker(running=True)
        tab._zip_import_flow._active_pitch_worker = w
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert w.cancel_called
        assert w.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_import_flow_laggard_defers_close(self, main_window):
        tab = _FakeSettingsTab()
        w = _FakeWorker(running=True, wait_result=False)
        tab._dict_import_flow._active_import_worker = w
        main_window.tabs.addTab(tab, "Settings")

        event = _trigger_close(main_window)

        assert w.cancel_called
        event.accept.assert_not_called()
        event.ignore.assert_called_once()


class TestCloseEventNoActiveWorkers:
    """Empty tab list should close cleanly without touching any worker."""

    def test_close_with_no_tabs(self, main_window):
        event = _trigger_close(main_window)
        event.accept.assert_called_once()


_WINDOW_OWNED_WORKER_ATTRS = ["validation_worker", "update_worker", "jmdict_migration_worker"]


@pytest.fixture
def quit_calls(monkeypatch):
    """Record QApplication.quit() calls made by the deferred-close machinery."""
    from anki_miner.gui.controllers import background_tasks as bg_module

    calls: list[bool] = []

    class _QuitRecorder:
        @staticmethod
        def quit() -> None:
            calls.append(True)

    monkeypatch.setattr(bg_module, "QApplication", _QuitRecorder)
    return calls


class TestCloseEventWindowOwnedWorkers:
    """closeEvent must join all four window-owned (controller-held) workers before accepting."""

    @pytest.mark.parametrize("attr", _WINDOW_OWNED_WORKER_ATTRS)
    def test_running_worker_cancelled_and_joined(self, main_window, attr):
        worker = _FakeWorker(running=True)
        setattr(main_window.background_tasks, attr, worker)

        event = _trigger_close(main_window)

        assert worker.cancel_called
        assert worker.wait_called_with == 2000
        event.accept.assert_called_once()

    def test_prewarm_worker_joined_without_timeout(self, main_window):
        worker = _FakePrewarmWorker(running=True)
        main_window.background_tasks.prewarm_worker = worker

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
        setattr(main_window.background_tasks, attr, worker)

        event = _trigger_close(main_window)

        assert worker.cancel_called  # cancel is still requested
        event.accept.assert_not_called()
        event.ignore.assert_called_once()
        assert main_window.isHidden()
        assert main_window.background_tasks._close_poll_timer is not None
        assert main_window.background_tasks._close_poll_timer.isActive()

    def test_tab_laggard_defers_close(self, main_window):
        tab = _FakeEpisodeTab(worker_running=True, wait_result=False)
        main_window.tabs.addTab(tab, "Episode")

        event = _trigger_close(main_window)

        assert tab.worker_thread.cancel_called
        event.accept.assert_not_called()
        event.ignore.assert_called_once()

    def test_tab_shutdown_runs_before_worker_join(self, main_window):
        """tab.shutdown() (gate poison) must precede the worker_thread join, so a
        worker parked in the curation gate is released first and does not time the
        join out into a spurious deferred close (F8)."""
        order: list[str] = []

        class _OrderWorker(_FakeWorker):
            def cancel(self) -> None:
                order.append("join-cancel")
                super().cancel()

        class _OrderTab(_FakeEpisodeTab):
            def shutdown(self) -> None:
                order.append("shutdown")

        tab = _OrderTab(worker_running=True, wait_result=True)
        tab.worker_thread = _OrderWorker(running=True, wait_result=True)
        main_window.tabs.addTab(tab, "Episode")

        _trigger_close(main_window)

        assert "shutdown" in order and "join-cancel" in order
        assert order.index("shutdown") < order.index("join-cancel"), f"shutdown must precede join; got {order}"

    def test_poll_keeps_app_alive_while_laggard_runs(self, quit_calls, main_window):
        worker = _FakeWorker(running=True, wait_result=False)
        main_window.background_tasks.update_worker = worker
        _trigger_close(main_window)

        main_window.background_tasks._poll_deferred_close()

        assert quit_calls == []
        assert main_window.background_tasks._close_poll_timer.isActive()

    def test_poll_quits_only_after_laggards_exit(self, quit_calls, main_window):
        worker = _FakeWorker(running=True, wait_result=False)
        main_window.background_tasks.update_worker = worker
        _trigger_close(main_window)

        worker.finish()
        main_window.background_tasks._poll_deferred_close()

        assert quit_calls == [True]
        assert not main_window.background_tasks._close_poll_timer.isActive()


# ---------------------------------------------------------------------------
# OVH-061 — closeEvent calls release_dictionary_resources after worker join
# ---------------------------------------------------------------------------


class TestCloseEventReleasesDictResources:
    """closeEvent must call release_dictionary_resources() before event.accept()
    so dict sqlite handles are freed deterministically on every idle shutdown
    (OVH-061 / Issue #30 Windows file-lock)."""

    def test_release_dict_resources_called_on_close(self, main_window, monkeypatch):
        """release_dictionary_resources() is called during idle closeEvent."""
        release_calls: list = []
        monkeypatch.setattr(
            main_window,
            "release_dictionary_resources",
            lambda: release_calls.append(True) or True,
        )

        event = _trigger_close(main_window)

        assert release_calls, "release_dictionary_resources() was not called at close"
        event.accept.assert_called_once()

    def test_release_dict_resources_after_worker_join(self, main_window, monkeypatch):
        """release_dictionary_resources() runs AFTER the worker join, not before.

        Calling it before is unsafe — a live thread might still be reading
        through the sqlite handles.
        """
        call_order: list[str] = []

        worker = _FakeWorker(running=True)
        real_cancel = worker.cancel
        real_wait = worker.wait

        def recording_cancel():
            call_order.append("cancel")
            return real_cancel()

        def recording_wait(*a, **kw):
            call_order.append("join")
            return real_wait(*a, **kw)

        worker.cancel = recording_cancel
        worker.wait = recording_wait

        monkeypatch.setattr(
            main_window,
            "release_dictionary_resources",
            lambda: call_order.append("release") or True,
        )

        # Insert the running worker via a fake tab.
        tab = _FakeEpisodeTab(worker_running=True)
        tab.worker_thread = worker
        main_window.tabs.addTab(tab, "Episode")

        _trigger_close(main_window)

        assert "join" in call_order
        assert "release" in call_order
        assert call_order.index("join") < call_order.index(
            "release"
        ), f"Expected join before release; got order={call_order}"

    def test_release_dict_resources_not_called_while_laggard_live(self, main_window, monkeypatch):
        """At defer time, with the laggard still live, release must NOT run yet —
        the thread is still reading through the handles (F7 releases later, in
        _poll_deferred_close, once the laggard exits)."""
        release_calls: list = []
        monkeypatch.setattr(
            main_window,
            "release_dictionary_resources",
            lambda: release_calls.append(True) or True,
        )

        # A laggard worker whose wait() times out → close is deferred.
        tab = _FakeEpisodeTab(worker_running=True, wait_result=False)
        main_window.tabs.addTab(tab, "Episode")

        event = _trigger_close(main_window)

        event.accept.assert_not_called()  # close is deferred
        assert release_calls == [], "release must not run while a laggard is still live"

    def test_release_dict_resources_called_on_deferred_close_completion(self, quit_calls, main_window, monkeypatch):
        """Once the deferred laggard exits, _poll_deferred_close releases dict
        resources before quitting — OVH-061 teardown is not skipped on the
        deferred path (F7)."""
        release_calls: list = []
        monkeypatch.setattr(
            main_window,
            "release_dictionary_resources",
            lambda: release_calls.append(True) or True,
        )

        worker = _FakeWorker(running=True, wait_result=False)
        main_window.background_tasks.update_worker = worker
        _trigger_close(main_window)
        assert release_calls == [], "must not release while laggard runs"

        worker.finish()
        main_window.background_tasks._poll_deferred_close()

        assert release_calls == [True], "release must run when the deferred close completes"
        assert quit_calls == [True]


class TestCloseEventFlushesSettingsAutosave:
    """closeEvent must flush a pending Settings auto-save BEFORE the shutdown
    fan-out stops the debounce timer, on BOTH close paths (immediate and
    deferred) — otherwise an edit made <1s before quit is silently dropped."""

    def _settings_tab(self, main_window, test_config, qtbot):
        """Insert a REAL SettingsTab (tab composition normally lives in app.py)
        and mirror app.py's config_changed → update_config wiring so a flushed
        commit actually reaches MainWindow.config."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        main_window.tabs.addTab(tab, "Settings")
        tab.config_changed.connect(lambda cfg: main_window.update_config(cfg, from_settings=True))
        return tab

    def test_flush_runs_before_background_shutdown(self, main_window, test_config, qtbot, monkeypatch):
        call_order: list[str] = []
        settings_tab = self._settings_tab(main_window, test_config, qtbot)
        monkeypatch.setattr(settings_tab, "flush_pending_settings", lambda: call_order.append("flush"))
        original_shutdown = main_window.background_tasks.shutdown
        monkeypatch.setattr(
            main_window.background_tasks,
            "shutdown",
            lambda tabs: call_order.append("shutdown") or original_shutdown(tabs),
        )

        _trigger_close(main_window)

        assert call_order[:2] == ["flush", "shutdown"]

    def test_flush_runs_on_deferred_close_path(self, main_window, test_config, qtbot, monkeypatch):
        flush_calls: list[bool] = []
        settings_tab = self._settings_tab(main_window, test_config, qtbot)
        monkeypatch.setattr(settings_tab, "flush_pending_settings", lambda: flush_calls.append(True))
        # A laggard worker whose wait() times out → close is deferred and
        # closeEvent returns before its final save_config.
        tab = _FakeEpisodeTab(worker_running=True, wait_result=False)
        main_window.tabs.addTab(tab, "Episode")

        event = _trigger_close(main_window)

        event.accept.assert_not_called()  # close was deferred...
        assert flush_calls == [True]  # ...but the flush already ran

    def test_pending_edit_persists_through_close(self, main_window, test_config, qtbot):
        """End-to-end: an armed debounce edit reaches MainWindow.config on close."""
        settings_tab = self._settings_tab(main_window, test_config, qtbot)
        settings_tab.deck_input.setText("EditedJustBeforeQuit")
        assert settings_tab._debounce_timer.isActive()

        _trigger_close(main_window)

        assert main_window.config.anki_deck_name == "EditedJustBeforeQuit"

"""The one composition order five workstreams share (D7, D16, D26, D39b).

Session restore, queue/download recovery, first-run setup, live wizard
readiness and restart-to-apply each landed a small hook rather than a boot
state machine of its own. Nothing owned the order those hooks run in, which is
what this module pins — the *relative* order, never a line number, so an edit
that preserves the sequence stays green and one that reorders it goes red.

The invariants, and why each is load-bearing:

* Fonts resolve after ``QApplication`` and before the first widget, so every
  widget is measured against the face it is drawn with (D44-B).
* Translators install before the first widget, because widgets capture their
  ``tr()`` strings at construction and language is restart-to-apply.
* The single-instance lock is taken before any window is composed and released
  only after ``app.exec()`` returns, so a relaunched child never meets the
  parent's lock or shares a live sqlite handle with it (D39b).
* Session state restores after every tab is registered — the saved route is
  addressed by stable key — and before the first paint, so the window is never
  drawn at one size and then jumped to another (D7).
* First-run setup is offered before any optional startup job, because boot used
  to start the JMdict migration and cancel it two lines later (D26).
* Session state saves once at the top of ``closeEvent``, before anything can
  hide the window or claim the one-shot boot slot (D7).
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

# Ordered steps of the application entry point. Each is a source fragment that
# occurs exactly once in ``app.main``; the test compares their positions.
MAIN_BOOT_STEPS = (
    "QApplication(sys.argv)",
    "initialize_application_fonts(app)",
    "install_translators(app",
    "_acquire_instance_lock(",
    "compose_main_window(",
    "window.commit_boot(",
    "window.show()",
    "exit_code = app.exec()",
    "_relaunch_if_requested(app)",
)


def _main_source() -> str:
    from anki_miner.gui import app as app_module

    return inspect.getsource(app_module.main)


class TestApplicationEntryPoint:
    def test_every_boot_step_appears_exactly_once(self) -> None:
        source = _main_source()

        counts = {step: source.count(step) for step in MAIN_BOOT_STEPS}

        assert counts == dict.fromkeys(MAIN_BOOT_STEPS, 1)

    def test_the_boot_steps_run_in_the_composed_order(self) -> None:
        source = _main_source()

        positions = [source.index(step) for step in MAIN_BOOT_STEPS]

        assert positions == sorted(
            positions
        ), "app.main reordered the boot sequence; the expected order is " + " -> ".join(MAIN_BOOT_STEPS)

    def test_the_instance_lock_is_never_released_inside_the_event_loop(self) -> None:
        """D39b: the lock is held for the process lifetime and dropped once.

        ``_relaunch_if_requested`` — which runs only after ``app.exec()`` has
        returned — is the sole place that unlocks it. An unlock anywhere in
        ``main`` would open a window in which two processes share the stores.
        """
        from anki_miner.gui import app as app_module

        assert "unlock(" not in _main_source()
        assert "unlock()" in inspect.getsource(app_module._relaunch_if_requested)

    def test_the_entry_point_starts_no_optional_worker_of_its_own(self) -> None:
        """Every optional startup job sits behind the one first-run gate.

        A job started from here instead is a job the first-run wizard cannot be
        made to precede, however carefully ``commit_boot`` is ordered.
        """
        assert "PrewarmWorker" not in _main_source()


class TestComposition:
    def test_the_session_restores_after_every_tab_and_before_the_first_paint(
        self, qtbot, patch_heavy_init, test_config, monkeypatch
    ) -> None:
        """D7's hook (W2-T7) is called from exactly one place, at the one point
        where the route can be resolved and the window has not been drawn yet."""
        from anki_miner.gui import app as app_module
        from anki_miner.gui.main_window import MainWindow

        patch_heavy_init(test_config)
        seen: list[tuple[int, bool]] = []

        def _record(self: MainWindow) -> None:
            seen.append((self.tabs.count(), self.isVisible()))

        monkeypatch.setattr(MainWindow, "restore_session_state", _record)

        window = app_module.compose_main_window(test_config).window
        qtbot.addWidget(window)

        assert len(seen) == 1, "the session must be restored from exactly one place"
        restored_tab_count, visible_at_restore = seen[0]
        assert restored_tab_count == window.tabs.count() == 7
        assert visible_at_restore is False
        window.deleteLater()

    def test_composition_starts_no_optional_boot_work(self, qtbot, patch_heavy_init, test_config) -> None:
        """Composition builds; ``commit_boot`` decides. Keeping the two apart is
        what lets first-run setup precede every optional job."""
        from anki_miner.gui import app as app_module

        patch_heavy_init(test_config)

        window = app_module.compose_main_window(test_config).window
        qtbot.addWidget(window)

        assert window._boot_committed is False
        assert window._post_setup_boot_started is False
        window.deleteLater()


class TestFirstRunPrecedesOptionalWork:
    @pytest.fixture
    def recorded_boot(self, qtbot, monkeypatch, test_config):
        """Build a window whose optional boot jobs and timers only record."""
        from anki_miner.gui import main_window as mw

        events: list[str] = []
        scheduled: list[object] = []

        monkeypatch.setattr(mw.ValidationService, "__init__", lambda self, *a, **kw: None)
        monkeypatch.setattr(mw.GUIConfigManager, "save_config", lambda cfg: None)
        monkeypatch.setattr(mw.MainWindow, "_maybe_repair_legacy_frequency_source_name", lambda self: None)
        monkeypatch.setattr(mw.MainWindow, "_maybe_migrate_legacy_pitch", lambda self: None)
        monkeypatch.setattr(mw.MainWindow, "_run_validation", lambda self: events.append("validation"))
        monkeypatch.setattr(mw.MainWindow, "_check_for_updates", lambda self: events.append("update"))
        monkeypatch.setattr(mw.MainWindow, "_maybe_migrate_jmdict", lambda self: events.append("migration"))
        monkeypatch.setattr(mw.MainWindow, "_maybe_start_ytdlp_update", lambda self: events.append("ytdlp"))
        monkeypatch.setattr(mw.QTimer, "singleShot", staticmethod(lambda _ms, fn: scheduled.append(fn)))

        def _build(*, first_run_done: bool):
            config = replace(
                test_config,
                last_known_version="",
                check_for_updates=True,
                first_run_shortcut_done=True,
                first_run_setup_done=first_run_done,
            )
            window = mw.MainWindow(config)
            qtbot.addWidget(window)
            return window

        yield _build, events, scheduled

    def test_a_first_run_starts_nothing_optional_until_the_wizard_exits(self, recorded_boot) -> None:
        build, events, scheduled = recorded_boot

        window = build(first_run_done=False)
        window.commit_boot()

        assert events == [], "optional boot work must wait behind first-run setup (D26)"
        assert window._maybe_offer_first_run_setup in scheduled
        window.deleteLater()

    def test_the_prewarm_waits_behind_first_run_setup(self, recorded_boot) -> None:
        """The last optional startup job that still raced the wizard.

        The tagger/dictionary prewarm opens every installed dictionary's sqlite
        index. Scheduled from ``app.main`` it fired inside the modal wizard's
        nested event loop, so a first run warmed the dictionary chain underneath
        the very Resources page that replaces it.

        It now waits behind the stale-resource scan as well — it holds the
        indexes a schema repair rebuilds — so the boot step schedules that scan
        and prewarm starts from its continuation. Either way nothing warms the
        chain until this one-shot step runs.
        """
        build, _events, scheduled = recorded_boot

        window = build(first_run_done=False)
        window.commit_boot()

        assert window._start_prewarm not in scheduled
        assert window._maybe_prompt_stale_resources not in scheduled

        window._start_post_setup_boot_once()

        assert window._maybe_prompt_stale_resources in scheduled
        # Not scheduled beside the scan: doing that is what made accepting the
        # stale-resource prompt refuse on every family.
        assert window._start_prewarm not in scheduled
        window.deleteLater()

    def test_a_normal_run_starts_the_optional_work_straight_away(self, recorded_boot) -> None:
        build, events, _ = recorded_boot

        window = build(first_run_done=True)
        window.commit_boot()

        assert events == ["validation", "update", "migration", "ytdlp"]
        window.deleteLater()


class TestShutdown:
    def test_the_session_is_saved_before_the_boot_slot_is_claimed_or_workers_join(
        self, qtbot, patch_heavy_init, test_config, monkeypatch
    ) -> None:
        """Everything after the save can hide the window or defer the close, and
        a hidden window's geometry is not what the user left behind (D7)."""
        from unittest.mock import MagicMock

        from PyQt6.QtCore import QEvent

        from anki_miner.gui import main_window as mw

        patch_heavy_init(test_config)
        window = mw.MainWindow()
        qtbot.addWidget(window)

        order: list[str] = []
        boot_slot_at_save: list[bool] = []

        original_save = window._save_session_state

        def _save() -> None:
            order.append("session")
            boot_slot_at_save.append(window._post_setup_boot_started)
            original_save()

        monkeypatch.setattr(window, "_save_session_state", _save)
        monkeypatch.setattr(
            window.background_tasks,
            "shutdown",
            lambda tabs: order.append("shutdown") or [],
        )
        monkeypatch.setattr(mw.GUIConfigManager, "save_config", lambda cfg: None)

        window.closeEvent(MagicMock(spec=QEvent))

        assert order == ["session", "shutdown"]
        assert boot_slot_at_save == [False]
        assert window._post_setup_boot_started is True
        window.deleteLater()

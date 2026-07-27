"""Tests for MainWindow first-run setup wiring (Task 3).

The first-run offer now launches the guided Setup Wizard (not the retired
WelcomeDialog). The re-entrancy guard, the broadened trigger (offer whenever
``not first_run_setup_done``), outcome-aware persistence, and the re-runnable
Tools → "Setup Wizard…" entry are all exercised here. The wizard itself is
monkeypatched so no Qt modal / AnkiConnect runs.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QWidget


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config, monkeypatch):
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    real_load_config = GUIConfigManager.load_config
    real_save_config = GUIConfigManager.save_config
    # Construct with the flag already set so __init__ does NOT schedule the
    # deferred QTimer that would otherwise fire the (unpatched) real first-run
    # offer during qtbot teardown and block on a real QWizard.exec(). Each test
    # flips first_run_setup_done back to False before invoking the method.
    construction_config = replace(test_config, first_run_setup_done=True)
    # stub_first_run_setup=False: _maybe_offer_first_run_setup is the method under
    # test, so it must stay real. Its __init__ trigger is a QTimer.singleShot(0,...)
    # that only fires once the event loop runs, which these synchronous unit tests
    # never spin, so leaving it real is safe.
    patch_heavy_init(construction_config, stub_first_run_setup=False)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    # The shared heavy-init fixture suppresses disk I/O during construction.
    # Restore the real methods after construction so these tests verify the
    # committed flags through the isolated GUIConfigManager file.
    monkeypatch.setattr(GUIConfigManager, "load_config", real_load_config)
    monkeypatch.setattr(GUIConfigManager, "save_config", real_save_config)
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _find_action(menu, text: str):
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def _tools_menu(window):
    menu_bar = window.menuBar()
    assert menu_bar is not None
    for action in menu_bar.actions():
        if action.text().replace("&", "") == "Tools":
            menu = action.menu()
            assert menu is not None
            return menu
    raise AssertionError("Tools menu not found")


def _wizard_outcome(config, *, consumes: bool):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizardOutcome

    return SetupWizardOutcome(config=config, consumes_first_run_offer=consumes)


# ---------------------------------------------------------------------------
# Tools menu entry
# ---------------------------------------------------------------------------


def test_tools_menu_has_setup_wizard_action(main_window):
    tools = _tools_menu(main_window)
    action = _find_action(tools, "Setup Wizard...")
    assert action is not None


def test_tools_setup_wizard_handler_calls_run_setup_wizard(main_window, monkeypatch):
    """The Tools handler runs run_setup_wizard and applies the result via update_config."""
    from anki_miner.gui import main_window as mw_module

    captured = {}

    def fake_run(parent, config):
        captured["config"] = config
        return _wizard_outcome(
            replace(config, anki_deck_name="Wizard Deck", first_run_setup_done=False),
            consumes=True,
        )

    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        fake_run,
    )
    applied = {}
    monkeypatch.setattr(
        mw_module.MainWindow,
        "update_config",
        lambda self, cfg, **kw: applied.__setitem__("cfg", cfg),
    )

    main_window._run_setup_wizard_tool()

    assert "config" in captured
    assert applied["cfg"].anki_deck_name == "Wizard Deck"
    # The Tools re-run must NOT touch the first_run flag.
    assert applied["cfg"].first_run_setup_done == captured["config"].first_run_setup_done


@pytest.mark.parametrize(
    ("state", "should_apply"),
    [
        pytest.param("success", True, id="success"),
        pytest.param("partial", True, id="partial"),
        pytest.param("cancelled", False, id="cancelled"),
        pytest.param("cancelled-partial", True, id="cancelled-partial"),
        pytest.param("failed", False, id="failed"),
    ],
)
def test_resource_activation_applies_only_successful_outcomes(main_window, monkeypatch, state, should_apply):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult, ResourceDownloadSummary

    success = ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")
    failure = ResourceDownloadResult("freq", "freq", "Frequency", "u", False, "network failed")
    results = {
        "success": [success],
        "partial": [success, failure],
        "cancelled": [],
        "cancelled-partial": [success],
        "failed": [failure],
    }[state]
    summary = ResourceDownloadSummary(
        results=results,
        cancelled=state.startswith("cancelled"),
        requested_count=3 if state.startswith("cancelled") else len(results),
    )
    updated = replace(main_window.config, anki_deck_name="Resources outcome applied")
    monkeypatch.setattr(
        "anki_miner.gui.utils.resource_setup.apply_download_summary",
        lambda _config, _summary: updated,
    )
    applied = []
    monkeypatch.setattr(main_window, "update_config", lambda config, **_kwargs: applied.append(config))

    returned = main_window._activate_downloaded_resources(summary)

    assert applied == ([updated] if should_apply else [])
    assert returned == (updated if should_apply else None)


def test_tools_resource_download_starts_in_the_background_without_a_modal(main_window, monkeypatch):
    """The Tools entry starts a session and returns; it no longer blocks the app."""
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    captured = {}

    def fake_start(parent, config, **kwargs):
        captured.update(kwargs)
        captured["parent"] = parent
        captured["config"] = config
        return MagicMock(task_id=dialog_mod.TASK_ID)

    monkeypatch.setattr(dialog_mod, "start_resource_download", fake_start)
    monkeypatch.setattr(main_window.background_tasks, "cancel_jmdict_migration", lambda: None)

    main_window._download_recommended_resources()

    assert captured["parent"] is main_window
    assert captured["activate"] == main_window._activate_downloaded_resources
    assert captured["task_registry"] is main_window.task_registry
    assert captured["adopt_worker"] == main_window.background_tasks.adopt_resource_download_worker
    assert main_window._resource_download_session is not None


def test_starting_the_download_takes_no_mutation_lease(main_window, monkeypatch, qtbot):
    """D15: the lease belongs to activation, not to the whole several-hundred-MB run."""
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    order: list[str] = []

    class FakeDictionaryPanel:
        def hold_mutation(self, kind):
            order.append(f"hold:{kind}")
            return object()

        def release(self, token):
            order.append("release")

    class FakeSettingsTab(QWidget):
        def __init__(self):
            super().__init__()
            self.dictionary_panel = FakeDictionaryPanel()

        def open_ui_subtab(self):
            return None

        def commit_pending_settings_for_mutation(self):
            order.append("preflight")
            return True

    settings_tab = FakeSettingsTab()
    qtbot.addWidget(settings_tab)
    main_window.tabs.addTab(settings_tab, "Settings")
    monkeypatch.setattr(main_window.background_tasks, "cancel_jmdict_migration", lambda: order.append("cancel"))
    monkeypatch.setattr(dialog_mod, "start_resource_download", lambda *a, **kw: order.append("start") or MagicMock())

    main_window._download_recommended_resources()

    assert order == ["cancel", "start"]


def test_activation_commits_pending_settings_before_reading_the_live_config(main_window, monkeypatch, qtbot, tmp_path):
    """Judge defect #8: a background run must not activate off a stale config base."""
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult, ResourceDownloadSummary

    new_root = tmp_path / "committed-root"
    new_root.mkdir()
    order: list[str] = []
    seen: list = []

    class FakeDictionaryPanel:
        def hold_mutation(self, kind):
            order.append(f"hold:{kind}")
            return object()

        def release(self, token):
            order.append("release")

    class FakeSettingsTab(QWidget):
        def __init__(self):
            super().__init__()
            self.dictionary_panel = FakeDictionaryPanel()

        def open_ui_subtab(self):
            return None

        def commit_pending_settings_for_mutation(self):
            order.append("preflight")
            main_window.config = replace(main_window.config, dicts_root=new_root)
            return True

    settings_tab = FakeSettingsTab()
    qtbot.addWidget(settings_tab)
    main_window.tabs.addTab(settings_tab, "Settings")

    def fake_apply(config, _summary):
        order.append("apply")
        seen.append(config)
        return config

    monkeypatch.setattr("anki_miner.gui.utils.resource_setup.apply_download_summary", fake_apply)
    monkeypatch.setattr(main_window, "update_config", lambda config, **_kw: order.append("persist"))

    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )
    main_window._activate_downloaded_resources(summary)

    assert order == ["preflight", "hold:resource-download", "apply", "persist", "release"]
    assert seen[0].dicts_root == new_root  # the committed edit, not the pre-download base


def test_activation_refused_by_settings_commit_returns_none(main_window, monkeypatch, qtbot):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult, ResourceDownloadSummary

    class FakeSettingsTab(QWidget):
        dictionary_panel = MagicMock()

        def open_ui_subtab(self):
            return None

        def commit_pending_settings_for_mutation(self):
            return False

    settings_tab = FakeSettingsTab()
    qtbot.addWidget(settings_tab)
    main_window.tabs.addTab(settings_tab, "Settings")
    applied = []
    monkeypatch.setattr(main_window, "update_config", lambda config, **_kw: applied.append(config))

    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )

    assert main_window._activate_downloaded_resources(summary) is None
    assert applied == []


# ---------------------------------------------------------------------------
# Same-slot race guard: a timed-out legacy JMdict XML migration refuses every
# dialog that can download into the same "jmdict-english" slot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "dialog_target"),
    [
        (
            "_download_recommended_resources",
            "anki_miner.gui.widgets.dialogs.resource_download_dialog.start_resource_download",
        ),
        ("_run_setup_wizard_tool", "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard"),
        ("_maybe_offer_first_run_setup", "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard"),
    ],
)
def test_handler_refuses_when_jmdict_migration_does_not_stop(main_window, monkeypatch, handler, dialog_target):
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(
        main_window.background_tasks,
        "prepare_dictionary_mutation",
        lambda: False,
    )
    dialog = MagicMock()
    monkeypatch.setattr(dialog_target, dialog)
    monkeypatch.setattr(mw_module.MainWindow, "update_config", lambda self, cfg, **kw: None)
    main_window._first_run_setup_handled = False
    main_window.config = replace(main_window.config, first_run_setup_done=False)

    getattr(main_window, handler)()

    dialog.assert_not_called()
    # The refusal is stated on the window rather than in a modal (D24).
    issue = main_window.issue_banner().current_issue()
    assert issue is not None
    assert "still stopping" in issue.summary


@pytest.mark.parametrize(
    ("handler", "dialog_target", "token_kind", "cancels_migration"),
    [
        (
            "_run_setup_wizard_tool",
            "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
            "setup-wizard",
            # Re-run from Tools: a JMdict migration really can be in flight, and
            # the wizard's Resources download writes into the same slot.
            True,
        ),
        (
            "_maybe_offer_first_run_setup",
            "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
            "first-run-setup-wizard",
            # First run: boot no longer starts the migration ahead of the offer,
            # so there is nothing to cancel (D26/T12). Boot starting work that
            # this handler immediately cancelled is the defect that removed it.
            False,
        ),
    ],
)
def test_handler_commits_pending_root_before_config_capture(
    main_window,
    qtbot,
    monkeypatch,
    tmp_path,
    handler,
    dialog_target,
    token_kind,
    cancels_migration,
):
    new_root = tmp_path / "committed-root"
    new_root.mkdir()
    order: list[str] = []

    class FakeDictionaryPanel:
        def hold_mutation(self, kind):
            order.append(f"hold:{kind}")
            return object()

        def release(self, token):
            order.append("release")

    class FakeSettingsTab(QWidget):
        def __init__(self):
            super().__init__()
            self.dictionary_panel = FakeDictionaryPanel()

        def open_ui_subtab(self):
            return None

        def commit_pending_settings_for_mutation(self):
            order.append("preflight")
            main_window.config = replace(main_window.config, dicts_root=new_root)
            return True

    settings_tab = FakeSettingsTab()
    qtbot.addWidget(settings_tab)
    main_window.tabs.addTab(settings_tab, "Settings")
    monkeypatch.setattr(main_window.background_tasks, "cancel_jmdict_migration", lambda: order.append("cancel"))

    def fake_dialog(parent, config, *args, **kwargs):
        order.append("dialog")
        assert config.dicts_root == new_root
        if "setup_wizard" in dialog_target:
            return _wizard_outcome(config, consumes=False)
        return None

    monkeypatch.setattr(dialog_target, fake_dialog)
    monkeypatch.setattr(type(main_window), "update_config", lambda self, cfg, **kw: None)
    main_window._first_run_setup_handled = False
    main_window.config = replace(main_window.config, first_run_setup_done=False)

    getattr(main_window, handler)()

    expected = ["preflight", f"hold:{token_kind}"]
    if cancels_migration:
        expected.append("cancel")
    assert order == [*expected, "dialog", "release"]


# ---------------------------------------------------------------------------
# First-run offer: commits partial config and consumes only explicit outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "consumes", "expected_done"),
    [
        pytest.param("accept", True, True, id="accept"),
        pytest.param("skip", True, True, id="explicit-skip"),
        pytest.param("x", False, False, id="window-close"),
        pytest.param("escape", False, False, id="escape"),
    ],
)
def test_first_run_outcome_persists_partial_config_and_consumption_flag(
    main_window,
    monkeypatch,
    action,
    consumes,
    expected_done,
):
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    calls = {"run": 0}

    def fake_run(parent, config):
        calls["run"] += 1
        return _wizard_outcome(replace(config, anki_note_type=f"Lapis-{action}"), consumes=consumes)

    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        fake_run,
    )

    main_window.config = replace(main_window.config, first_run_setup_done=False)
    GUIConfigManager.save_config(main_window.config)
    main_window._first_run_setup_handled = False
    main_window._maybe_offer_first_run_setup()

    persisted = GUIConfigManager.load_config()
    assert calls["run"] == 1
    assert persisted.first_run_setup_done is expected_done
    assert persisted.anki_note_type == f"Lapis-{action}"


def test_first_run_exception_is_logged_without_consuming_offer(main_window, monkeypatch, caplog):
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    def boom(parent, config):
        raise RuntimeError("wizard exploded")

    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        boom,
    )
    main_window.config = replace(main_window.config, first_run_setup_done=False)
    GUIConfigManager.save_config(main_window.config)
    main_window._first_run_setup_handled = False
    with caplog.at_level("ERROR"):
        main_window._maybe_offer_first_run_setup()

    persisted = GUIConfigManager.load_config()
    assert persisted.first_run_setup_done is False
    assert main_window.config.first_run_setup_done is False
    assert "Setup wizard failed" in caplog.text


def test_first_run_commit_merges_shortcut_flag_set_during_wizard(main_window, monkeypatch):
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    main_window.config = replace(
        main_window.config,
        first_run_setup_done=False,
        first_run_shortcut_done=False,
    )
    GUIConfigManager.save_config(main_window.config)

    def fake_run(parent, wizard_snapshot):
        # Simulate the shortcut worker finishing inside QWizard.exec()'s nested
        # event loop. The outcome still carries the launch-time stale False.
        parent.update_config(replace(parent.config, first_run_shortcut_done=True))
        assert wizard_snapshot.first_run_shortcut_done is False
        return _wizard_outcome(
            replace(wizard_snapshot, anki_note_type="Race Winner"),
            consumes=True,
        )

    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        fake_run,
    )
    main_window._first_run_setup_handled = False
    main_window._maybe_offer_first_run_setup()

    persisted = GUIConfigManager.load_config()
    assert persisted.first_run_shortcut_done is True
    assert persisted.first_run_setup_done is True
    assert persisted.anki_note_type == "Race Winner"


def test_first_run_reentrancy_guard_blocks_second_call(main_window, monkeypatch):
    calls = {"run": 0}

    def fake_run(parent, config):
        calls["run"] += 1
        return _wizard_outcome(config, consumes=False)

    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        fake_run,
    )
    monkeypatch.setattr(
        type(main_window),
        "update_config",
        lambda self, cfg, **kw: None,
    )

    main_window.config = replace(main_window.config, first_run_setup_done=False)
    main_window._first_run_setup_handled = False
    main_window._maybe_offer_first_run_setup()
    main_window._maybe_offer_first_run_setup()  # guarded — must not run twice

    assert calls["run"] == 1


def test_first_run_offer_triggers_regardless_of_resource_files(main_window, monkeypatch, tmp_path):
    """Broadened trigger: the wizard is offered whenever not first_run_setup_done.

    Even when a pitch file already exists (an existing user), the wizard is
    still offered — the resource step is just skippable.
    """
    pitch = tmp_path / "pitch_accent.csv"
    pitch.write_text("x,0\n")

    calls = {"run": 0}
    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        lambda parent, config: (
            calls.__setitem__("run", calls["run"] + 1),
            _wizard_outcome(config, consumes=False),
        )[1],
    )
    monkeypatch.setattr(
        type(main_window),
        "update_config",
        lambda self, cfg, **kw: None,
    )

    main_window.config = replace(
        main_window.config,
        first_run_setup_done=False,
        pitch_accent_path=pitch,
    )
    main_window._first_run_setup_handled = False
    main_window._maybe_offer_first_run_setup()

    assert calls["run"] == 1


# ---------------------------------------------------------------------------
# The first action, and the one-time optional-boot slot (D26 / T12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("open_video", [True, False])
def test_accepted_finish_is_the_only_outcome_that_navigates(main_window, monkeypatch, open_video):
    from anki_miner.gui.capabilities import CapabilityTarget
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizardOutcome

    revealed: list[CapabilityTarget] = []
    monkeypatch.setattr(type(main_window), "reveal_capability", lambda self, target: revealed.append(target))
    committed: list[object] = []
    monkeypatch.setattr(type(main_window), "update_config", lambda self, cfg, **kw: committed.append(cfg))

    main_window._commit_setup_wizard_outcome(
        SetupWizardOutcome(
            config=main_window.config,
            consumes_first_run_offer=True,
            open_video_mining=open_video,
        ),
        first_run_offer=True,
    )

    assert revealed == ([CapabilityTarget("video", "single")] if open_video else [])
    # The config is committed before the screen that reads it is shown.
    assert len(committed) == 1


def _record_optional_boot_jobs(main_window, monkeypatch):
    started: list[str] = []
    for name in ("_run_validation", "_check_for_updates", "_maybe_migrate_jmdict", "_maybe_start_ytdlp_update"):
        monkeypatch.setattr(type(main_window), name, lambda self, _n=name: started.append(_n))
    return started


def test_optional_boot_work_starts_once_however_many_times_it_is_asked(main_window, monkeypatch):
    started = _record_optional_boot_jobs(main_window, monkeypatch)
    main_window._post_setup_boot_started = False

    main_window._start_post_setup_boot_once()
    main_window._start_post_setup_boot_once()

    assert started == ["_run_validation", "_check_for_updates", "_maybe_migrate_jmdict", "_maybe_start_ytdlp_update"]


@pytest.mark.parametrize("consumes", [True, False])
def test_every_wizard_exit_releases_the_deferred_boot_work(main_window, monkeypatch, consumes):
    """Skip, Escape and the window close must not leave the app half-booted."""
    started = _record_optional_boot_jobs(main_window, monkeypatch)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        lambda parent, config: _wizard_outcome(config, consumes=consumes),
    )
    monkeypatch.setattr(type(main_window), "update_config", lambda self, cfg, **kw: None)
    main_window.config = replace(main_window.config, first_run_setup_done=False)
    main_window._first_run_setup_handled = False
    main_window._post_setup_boot_started = False

    main_window._maybe_offer_first_run_setup()

    assert "_run_validation" in started


def test_a_wizard_that_raises_still_releases_the_deferred_boot_work(main_window, monkeypatch):
    started = _record_optional_boot_jobs(main_window, monkeypatch)

    def boom(parent, config):
        raise RuntimeError("wizard exploded")

    monkeypatch.setattr("anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard", boom)
    main_window.config = replace(main_window.config, first_run_setup_done=False)
    main_window._first_run_setup_handled = False
    main_window._post_setup_boot_started = False

    main_window._maybe_offer_first_run_setup()

    assert "_run_validation" in started


def test_a_mutation_refusal_still_releases_the_deferred_boot_work(main_window, monkeypatch):
    from contextlib import contextmanager

    started = _record_optional_boot_jobs(main_window, monkeypatch)

    @contextmanager
    def refuse(_kind):
        yield False

    monkeypatch.setattr(type(main_window), "_dictionary_mutation_guard", lambda self, kind: refuse(kind))
    main_window.config = replace(main_window.config, first_run_setup_done=False)
    main_window._first_run_setup_handled = False
    main_window._post_setup_boot_started = False

    main_window._maybe_offer_first_run_setup()

    assert "_run_validation" in started
    # A refusal must not consume the one-time offer either.
    assert main_window._first_run_setup_handled is False


def test_shutdown_starts_no_late_optional_boot_work(main_window, monkeypatch, qtbot):
    from PyQt6.QtGui import QCloseEvent

    started = _record_optional_boot_jobs(main_window, monkeypatch)
    monkeypatch.setattr(main_window.background_tasks, "shutdown", lambda tabs: [])
    main_window.closeEvent(QCloseEvent())

    main_window._start_post_setup_boot_once()

    assert started == []

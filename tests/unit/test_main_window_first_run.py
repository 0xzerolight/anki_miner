"""Tests for MainWindow first-run setup wiring (Task 3).

The first-run offer now launches the guided Setup Wizard (not the retired
WelcomeDialog). The re-entrancy guard, the broadened trigger (offer whenever
``not first_run_setup_done``), the ``finally``-persist of the flag, and the
re-runnable Tools → "Setup Wizard…" entry are all exercised here. The wizard
itself is monkeypatched so no Qt modal / AnkiConnect runs.
"""

from __future__ import annotations

from dataclasses import replace

import pytest


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
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
        return replace(config, anki_deck_name="Wizard Deck")

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


# ---------------------------------------------------------------------------
# First-run offer: launches the wizard, persists the flag in finally
# ---------------------------------------------------------------------------


def test_first_run_offer_launches_wizard_and_persists_flag(main_window, monkeypatch):
    from anki_miner.gui import main_window as mw_module

    calls = {"run": 0}

    def fake_run(parent, config):
        calls["run"] += 1
        return replace(config, anki_note_type="Lapis")

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

    main_window.config = replace(main_window.config, first_run_setup_done=False)
    main_window._first_run_setup_handled = False
    main_window._maybe_offer_first_run_setup()

    assert calls["run"] == 1
    assert applied["cfg"].first_run_setup_done is True
    assert applied["cfg"].anki_note_type == "Lapis"


def test_first_run_offer_persists_flag_even_if_wizard_raises(main_window, monkeypatch):
    from anki_miner.gui import main_window as mw_module

    def boom(parent, config):
        raise RuntimeError("wizard exploded")

    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard",
        boom,
    )
    applied = {}
    monkeypatch.setattr(
        mw_module.MainWindow,
        "update_config",
        lambda self, cfg, **kw: applied.__setitem__("cfg", cfg),
    )

    main_window.config = replace(main_window.config, first_run_setup_done=False)
    main_window._first_run_setup_handled = False
    with pytest.raises(RuntimeError):
        main_window._maybe_offer_first_run_setup()

    # The finally must still persist the flag so the wizard never re-fires.
    assert applied["cfg"].first_run_setup_done is True


def test_first_run_reentrancy_guard_blocks_second_call(main_window, monkeypatch):
    calls = {"run": 0}

    def fake_run(parent, config):
        calls["run"] += 1
        return config

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
        lambda parent, config: (calls.__setitem__("run", calls["run"] + 1), config)[1],
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

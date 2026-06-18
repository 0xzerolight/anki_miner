"""Tests for :class:`MainWindow` tab-switching keyboard shortcuts.

After the OVH-021 refactor the per-tab Ctrl+N shortcuts are created by
``setup_tab_shortcuts()``, which app.py calls after all tabs are registered.
``_setup_shortcuts`` (runs in ``__init__`` before any tabs exist) only wires
Ctrl+T / Ctrl+, / Ctrl+Shift+V.  The tests here cover both the count-driven
shortcut set and the guarantee of exactly-one-per-tab with no gaps.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
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


def _shortcut_keys(window) -> set[str]:
    """Return the portable-text key sequences of every QShortcut on the window."""
    return {sc.key().toString(QKeySequence.SequenceFormat.PortableText) for sc in window.findChildren(QShortcut)}


def _add_dummy_tabs(window, qtbot, n: int) -> None:
    """Add *n* plain QWidgets as tabs so setup_tab_shortcuts() sees the right count."""
    for i in range(n):
        dummy = QWidget()
        qtbot.addWidget(dummy)
        window.tabs.addTab(dummy, f"Tab {i + 1}")


def test_setup_shortcuts_does_not_create_tab_shortcuts(main_window):
    """_setup_shortcuts (called in __init__) must NOT create any Ctrl+N tab shortcuts."""
    keys = _shortcut_keys(main_window)
    # No tabs registered yet — none of Ctrl+1..9 should appear.
    for i in range(1, 10):
        assert f"Ctrl+{i}" not in keys, f"Ctrl+{i} was created in __init__ before tabs are registered"


def test_setup_tab_shortcuts_creates_one_per_tab(main_window, qtbot):
    """After setup_tab_shortcuts(), exactly one Ctrl+N shortcut exists per tab."""
    _add_dummy_tabs(main_window, qtbot, 7)
    main_window.setup_tab_shortcuts()

    keys = _shortcut_keys(main_window)
    for i in range(1, 8):
        assert f"Ctrl+{i}" in keys, f"missing Ctrl+{i} after registering 7 tabs"
    # No 8th shortcut — count-driven, not fixed range(1,8)
    assert "Ctrl+8" not in keys


def test_setup_tab_shortcuts_count_driven_not_fixed(main_window, qtbot):
    """Shortcut count tracks actual tab count, not a hardcoded constant."""
    _add_dummy_tabs(main_window, qtbot, 4)
    main_window.setup_tab_shortcuts()

    keys = _shortcut_keys(main_window)
    for i in range(1, 5):
        assert f"Ctrl+{i}" in keys, f"missing Ctrl+{i} for 4-tab window"
    assert "Ctrl+5" not in keys


def test_setup_tab_shortcuts_no_duplicates(main_window, qtbot):
    """Each Ctrl+N shortcut appears exactly once — no duplicates."""
    _add_dummy_tabs(main_window, qtbot, 3)
    main_window.setup_tab_shortcuts()

    all_shortcuts = main_window.findChildren(QShortcut)
    tab_shortcuts = [
        sc
        for sc in all_shortcuts
        if sc.key().toString(QKeySequence.SequenceFormat.PortableText).startswith("Ctrl+")
        and sc.key().toString(QKeySequence.SequenceFormat.PortableText)[5:].isdigit()
    ]
    keys_list = [sc.key().toString(QKeySequence.SequenceFormat.PortableText) for sc in tab_shortcuts]
    assert len(keys_list) == len(set(keys_list)), f"Duplicate tab shortcuts: {keys_list}"


def test_ctrl_t_ctrl_comma_ctrl_shift_v_still_wired(main_window):
    """Non-tab shortcuts (Ctrl+T, Ctrl+,, Ctrl+Shift+V) are created in __init__."""
    keys = _shortcut_keys(main_window)
    assert "Ctrl+T" in keys, "Ctrl+T (theme cycle) missing from __init__ shortcuts"
    assert "Ctrl+," in keys, "Ctrl+, (settings) missing from __init__ shortcuts"
    assert "Ctrl+Shift+V" in keys, "Ctrl+Shift+V (validation) missing from __init__ shortcuts"


def test_tab_switch_shortcut_activates_correct_tab(main_window, qtbot):
    """Activating Ctrl+N via _switch_to_tab() selects the correct tab index."""
    _add_dummy_tabs(main_window, qtbot, 3)
    main_window.setup_tab_shortcuts()

    main_window.tabs.setCurrentIndex(0)
    main_window._switch_to_tab(2)
    assert main_window.tabs.currentIndex() == 2


def test_about_dialog_lists_seven_tab_shortcuts():
    """The About card advertises Ctrl+1..7, matching the seven wired tab shortcuts."""
    from anki_miner.gui.widgets.dialogs.about_dialog import ABOUT_SHORTCUTS

    labels = dict(ABOUT_SHORTCUTS)
    assert labels.get("Ctrl+1..7") == "Switch tabs"

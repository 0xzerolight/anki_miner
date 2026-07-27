"""Tests for :class:`MainWindow` tab-switching keyboard shortcuts.

After the OVH-021 refactor the per-tab Ctrl+N shortcuts are created by
``setup_tab_shortcuts()``, which app.py calls after all tabs are registered.
``_setup_shortcuts`` (runs in ``__init__`` before any tabs exist) wires only
Ctrl+, -- D48-B dropped Ctrl+T and Ctrl+Shift+V because both collide with a
binding every other desktop application already owns, and both had a visible
control doing the same job. The tests here cover the count-driven shortcut set,
the guarantee of exactly-one-per-tab with no gaps, and the rule that the About
card is generated from the same constants the window installs from.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.utils.keyboard_shortcuts import PRIMARY_ACTION_DISPLAY


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    patch_heavy_init(test_config)
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


def test_settings_shortcut_still_wired(main_window):
    """Ctrl+, survives: it is the one global binding that collides with nothing."""
    keys = _shortcut_keys(main_window)
    assert "Ctrl+," in keys, "Ctrl+, (settings) missing from __init__ shortcuts"


@pytest.mark.parametrize(
    ("sequence", "standard_meaning"),
    [
        ("Ctrl+T", "new tab"),
        ("Ctrl+Shift+V", "paste as plain text"),
    ],
)
def test_shortcuts_colliding_with_standard_bindings_are_gone(main_window, sequence, standard_meaning):
    """D48-B fixes the conflicts.

    Both had a visible control doing the same job -- the header's favourites
    combo and Settings' validation button -- so the binding was the only thing
    that had to go.
    """
    keys = _shortcut_keys(main_window)
    assert sequence not in keys, f"{sequence} still bound; it is {standard_meaning} everywhere else"


def test_f1_opens_the_feature_browser_not_about(main_window):
    """F1 is Help on every desktop, and About is not help (D48-B)."""
    actions = {action.text(): action for action in main_window.findChildren(QAction)}
    find_feature = next((a for text, a in actions.items() if "Find a Feature" in text), None)
    about = next((a for text, a in actions.items() if "About" in text), None)

    assert find_feature is not None, "Find a Feature action missing"
    assert about is not None, "About action missing"
    assert find_feature.shortcut().toString(QKeySequence.SequenceFormat.PortableText) == "F1"
    assert about.shortcut().isEmpty(), "About must not hold a shortcut of its own"


def test_tab_switch_shortcut_activates_correct_tab(main_window, qtbot):
    """Activating Ctrl+N via _switch_to_tab() selects the correct tab index."""
    _add_dummy_tabs(main_window, qtbot, 3)
    main_window.setup_tab_shortcuts()

    main_window.tabs.setCurrentIndex(0)
    main_window._switch_to_tab(2)
    assert main_window.tabs.currentIndex() == 2


def test_about_dialog_lists_seven_tab_shortcuts():
    """The About card advertises Ctrl+1..7, matching the seven wired tab shortcuts."""
    from anki_miner.gui.utils.keyboard_shortcuts import SHORTCUT_HELP

    labels = dict(SHORTCUT_HELP)
    assert labels.get("Ctrl+1..7") == "Switch tabs"


def test_about_reads_its_rows_from_the_shortcut_constants():
    """About prints the same table the window installs from -- no second copy.

    The two used to be independent literals, which is how About kept
    advertising F1 for itself after F1 had become Help.
    """
    from anki_miner.gui.utils.keyboard_shortcuts import SHORTCUT_HELP
    from anki_miner.gui.widgets.dialogs import about_dialog

    assert about_dialog.ABOUT_SHORTCUTS is SHORTCUT_HELP


def test_advertised_global_bindings_are_the_installed_ones(main_window):
    """Every non-parametric row About prints is really bound on the window."""
    from anki_miner.gui.utils.keyboard_shortcuts import SHORTCUT_HELP

    installed = _shortcut_keys(main_window)
    menu_keys = {
        action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
        for action in main_window.findChildren(QAction)
        if not action.shortcut().isEmpty()
    }
    reachable = installed | menu_keys
    # Ctrl+1..7 is a range and Ctrl+Enter is per-screen, so neither is a literal
    # window binding; every other advertised row must be.
    for keys, _description in SHORTCUT_HELP:
        if ".." in keys or keys == PRIMARY_ACTION_DISPLAY:
            continue
        assert keys in reachable, f"About advertises {keys} but nothing binds it"


def test_about_does_not_advertise_a_removed_binding():
    """The removed collisions must vanish from the card too, not just the window."""
    from anki_miner.gui.utils.keyboard_shortcuts import SHORTCUT_HELP

    advertised = {keys for keys, _ in SHORTCUT_HELP}
    assert "Ctrl+T" not in advertised
    assert "Ctrl+Shift+V" not in advertised

"""MainWindow's window-wide shortcuts, keyed from Settings -> Keyboard.

``_setup_shortcuts`` creates one QShortcut per app action (Open Settings, and one
per main tab by stable tab key); the Usage Guide menu action carries its own.
``_apply_key_bindings`` keys them all from ``config.key_bindings`` at
construction and again on every ``update_config``, so a rebinding is live. D48-B
dropped Ctrl+T and Ctrl+Shift+V. The About card is generated from the same live
bindings (``key_bindings.about_rows``).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.capabilities import CAPABILITIES, MAIN_TAB_ORDER
from anki_miner.gui.utils.key_bindings import about_rows, resolve_bindings
from anki_miner.gui.utils.keyboard_shortcuts import PRIMARY_ACTION_DISPLAY

PORTABLE = QKeySequence.SequenceFormat.PortableText


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
    return {sc.key().toString(PORTABLE) for sc in window.findChildren(QShortcut)}


def _capture_about(monkeypatch) -> list[list[tuple[str, str]]]:
    """Swap AboutDialog for a recorder of the rows MainWindow hands it."""
    from anki_miner.gui.widgets.dialogs import about_dialog

    captured: list[list[tuple[str, str]]] = []

    class _Recorder:
        def __init__(self, version, shortcuts, parent=None):
            captured.append(list(shortcuts))

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(about_dialog, "AboutDialog", _Recorder)
    return captured


def test_one_shortcut_per_main_tab_with_the_shipped_keys(main_window):
    keys = _shortcut_keys(main_window)
    for number in range(1, len(MAIN_TAB_ORDER) + 1):
        assert f"Ctrl+{number}" in keys, f"missing Ctrl+{number}"
    assert "Ctrl+8" not in keys


def test_tab_shortcuts_are_not_duplicated(main_window):
    keys = [sc.key().toString(PORTABLE) for sc in main_window.findChildren(QShortcut)]
    tab_keys = [key for key in keys if key.startswith("Ctrl+") and key[5:].isdigit()]
    assert len(tab_keys) == len(set(tab_keys)) == len(MAIN_TAB_ORDER)


def test_tab_order_matches_the_bindings(wired_window):
    """Ctrl+N is the Nth tab only while MAIN_TAB_ORDER is the order the tabs are registered in."""
    from anki_miner.gui.main_window import MainWindow

    window, _titles, _tabs = wired_window
    assert tuple(MainWindow._MAIN_TAB_CLASSES) == MAIN_TAB_ORDER
    assert [window._main_tab_index(key) for key in MAIN_TAB_ORDER] == list(range(len(MAIN_TAB_ORDER)))


def test_each_tab_shortcut_opens_its_own_tab(wired_window):
    """Each one looks its tab up by stable key when it fires, never by a remembered index."""
    window, _titles, _tabs = wired_window
    for tab_key in MAIN_TAB_ORDER:
        window.tabs.setCurrentIndex(1 if tab_key == MAIN_TAB_ORDER[0] else 0)
        window._tab_shortcuts[tab_key].activated.emit()
        assert window._current_main_tab_key() == tab_key


def test_settings_shortcut_still_wired(main_window):
    """Ctrl+, survives by default: it collides with nothing."""
    assert "Ctrl+," in _shortcut_keys(main_window)


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
    assert sequence not in _shortcut_keys(main_window), f"{sequence} still bound; it is {standard_meaning} elsewhere"


def test_f1_opens_the_usage_guide_not_about(main_window):
    """F1 is Help on every desktop, and About is not help (D48-B)."""
    actions = {action.text(): action for action in main_window.findChildren(QAction)}
    about = next((a for text, a in actions.items() if "About" in text), None)
    guide = main_window.usage_guide_action

    assert about is not None, "About action missing"
    assert guide.shortcut().toString(PORTABLE) == "F1"
    assert about.shortcut().isEmpty(), "About must not hold a shortcut of its own"


def test_about_lists_the_seven_tab_shortcuts_as_one_row(qapp):
    assert dict(about_rows(resolve_bindings({}))).get("Ctrl+1..7") == "Switch tabs"


def test_advertised_global_bindings_are_the_installed_ones(main_window):
    """Every non-parametric row About prints is really bound on the window."""
    installed = _shortcut_keys(main_window)
    menu_keys = {
        action.shortcut().toString(PORTABLE)
        for action in main_window.findChildren(QAction)
        if not action.shortcut().isEmpty()
    }
    reachable = installed | menu_keys
    # Ctrl+1..7 is a range and Ctrl+Enter is per-screen, so neither is a literal
    # window binding; every other advertised row must be.
    for keys, _description in about_rows(resolve_bindings(main_window.config.key_bindings)):
        if ".." in keys or keys == PRIMARY_ACTION_DISPLAY:
            continue
        assert keys in reachable, f"About advertises {keys} but nothing binds it"


def test_about_does_not_advertise_a_removed_binding(qapp):
    advertised = {keys for keys, _ in about_rows(resolve_bindings({}))}
    assert "Ctrl+T" not in advertised
    assert "Ctrl+Shift+V" not in advertised


def test_a_rebinding_is_live_without_a_restart(main_window):
    main_window.update_config(
        replace(main_window.config, key_bindings={"app.open_settings": "Ctrl+Shift+S", "app.usage_guide": ""})
    )
    keys = _shortcut_keys(main_window)
    assert "Ctrl+Shift+S" in keys
    assert "Ctrl+," not in keys
    assert main_window.usage_guide_action.shortcut().isEmpty()


def test_about_is_built_from_the_live_bindings(main_window, monkeypatch):
    captured = _capture_about(monkeypatch)
    main_window.update_config(replace(main_window.config, key_bindings={"app.open_settings": "Ctrl+Shift+S"}))
    main_window._show_about()
    assert ("Ctrl+Shift+S", "Open Settings") in captured[0]
    assert all(keys != "Ctrl+," for keys, _ in captured[0])


def test_rebinding_open_settings_in_the_panel_is_live_everywhere(wired_window, qtbot, monkeypatch):
    """Review Focus 4: Settings -> Keyboard, then the window, About and the Usage Guide."""
    window, _titles, _tabs = wired_window
    settings_tab = window.tabs.widget(window._main_tab_index("settings"))
    # Showing the window below makes a later reveal_capability() switch to
    # Settings a real showEvent, which SettingsTab.showEvent otherwise answers
    # with a live AnkiConnect probe (see its docstring).
    monkeypatch.setattr(settings_tab._anki_probe, "refresh_name_lists", lambda: None)

    assert settings_tab.keyboard_panel.set_binding("app.open_settings", QKeySequence("Ctrl+Shift+S"))

    # Committed through SettingsTab -> MainWindow.update_config, and re-keyed in place.
    assert window.config.key_bindings == {"app.open_settings": "Ctrl+Shift+S"}
    keys = _shortcut_keys(window)
    assert "Ctrl+Shift+S" in keys
    assert "Ctrl+," not in keys

    # A real key press opens Settings from another tab.
    window.show()
    qtbot.waitExposed(window)
    QApplication.setActiveWindow(window)
    window.tabs.setCurrentIndex(0)
    QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert window._current_main_tab_key() == "settings"

    # About prints the new key.
    captured = _capture_about(monkeypatch)
    window._show_about()
    assert ("Ctrl+Shift+S", "Open Settings") in captured[0]

    # The Usage Guide entry opens the page that shows it.
    entry = next(cap for cap in CAPABILITIES if cap.id == "keyboard-shortcuts")
    window.tabs.setCurrentIndex(0)
    window.reveal_capability(entry.target)
    assert window._current_main_tab_key() == "settings"
    assert settings_tab.current_subtab_key() == "keyboard"
    shown = settings_tab.keyboard_panel._editors["app.open_settings"].keySequence()
    assert shown.toString(PORTABLE) == "Ctrl+Shift+S"
    window.hide()

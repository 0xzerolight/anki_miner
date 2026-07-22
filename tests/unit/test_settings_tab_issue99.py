"""Issue #99: settings-panel scroll-through fix + safe Reset-to-Defaults.

Two behaviors:
  1. Every spin/combo in the settings tab ignores hover-scroll (StrongFocus +
     the no-scroll event filter installed once per scroll area).
  2. A Reset-to-Defaults button that is safe: confirm defaults to No, no
     keyboard shortcut, and preservation of machine-specific fields *and* the
     UI-appearance fields so it can't wipe installed dictionaries or the theme.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QMessageBox

from anki_miner.config import AnkiMinerConfig, ChainEntry, create_default_config
from anki_miner.gui.widgets.settings_tab import SettingsTab

# --- Part 1: scroll-through fix wired into every settings scroll area ---


def test_all_settings_inputs_have_strong_focus(test_config: AnkiMinerConfig, qtbot):
    """Every spin/combo in the tab is StrongFocus (default is WheelFocus), proving
    install_no_scroll_on_inputs ran on all nine scroll-wrapped panels."""
    tab = SettingsTab(test_config)
    qtbot.addWidget(tab)

    inputs = [*tab.findChildren(QAbstractSpinBox), *tab.findChildren(QComboBox)]
    assert inputs, "expected spin/combo widgets in the settings tab"
    offenders = [w for w in inputs if w.focusPolicy() != Qt.FocusPolicy.StrongFocus]
    assert not offenders, f"{len(offenders)} input(s) still steal wheel focus"


# --- Part 2: safe Reset-to-Defaults ---


def _seed_config(test_config: AnkiMinerConfig) -> AnkiMinerConfig:
    """A config whose asserted fields all DIFFER from their defaults, so the
    reset/preserve assertions are falsifiable (not vacuous 1.0==1.0)."""
    return replace(
        test_config,
        screenshot_offset=3.5,  # reset target (default 1.0)
        theme="dark",  # UI-appearance preserve guard (default "light")
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="seed-dict", enabled=True),
            *test_config.dictionary_chain,
        ),  # machine-specific preserve
    )


def test_reset_button_exists_without_shortcut(test_config: AnkiMinerConfig, qtbot):
    """The button is present; no Ctrl+R (or any) shortcut is wired to reset."""
    tab = SettingsTab(test_config)
    qtbot.addWidget(tab)

    assert hasattr(tab, "reset_settings_button")
    from PyQt6.QtGui import QShortcut

    shortcuts = {sc.key().toString() for sc in tab.findChildren(QShortcut)}
    assert "Ctrl+R" not in shortcuts


def test_reset_confirmed_resets_and_preserves(test_config: AnkiMinerConfig, qtbot, monkeypatch):
    """Confirmed reset: behaviour → defaults, machine-specific + UI kept, persisted once."""
    tab = SettingsTab(_seed_config(test_config))
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    emitted: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emitted.append)

    tab._on_reset_to_defaults_clicked()

    assert len(emitted) == 1, "reset must persist exactly once"
    new = emitted[0]
    defaults = create_default_config()
    assert new.screenshot_offset == defaults.screenshot_offset  # reset
    assert new.theme == "dark"  # UI-appearance preserved (fails if _RESET_PRESERVE_UI emptied)
    assert any(e.dict_id == "seed-dict" for e in new.dictionary_chain)  # machine-specific preserved


def test_reset_declined_does_nothing(test_config: AnkiMinerConfig, qtbot, monkeypatch):
    """Declining the confirm leaves config untouched and emits nothing."""
    tab = SettingsTab(_seed_config(test_config))
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    before = tab.config
    emitted: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emitted.append)

    tab._on_reset_to_defaults_clicked()

    assert emitted == []
    assert tab.config is before

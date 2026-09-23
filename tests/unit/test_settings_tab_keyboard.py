"""SettingsTab wiring for the Keyboard page (App group, beside Appearance & Language)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab

PORTABLE = QKeySequence.SequenceFormat.PortableText


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def _shown(tab: SettingsTab, action_id: str) -> str:
    return tab.keyboard_panel._editors[action_id].keySequence().toString(PORTABLE)


def test_an_accepted_key_commits_at_once(tab, qtbot):
    with qtbot.waitSignal(tab.config_changed, timeout=1000) as blocker:
        assert tab.keyboard_panel.set_binding("app.open_settings", QKeySequence("Ctrl+Shift+S"))
    assert blocker.args[0].key_bindings == {"app.open_settings": "Ctrl+Shift+S"}


def test_a_refused_key_commits_nothing(tab, qtbot):
    with qtbot.assertNotEmitted(tab.config_changed):
        assert not tab.keyboard_panel.set_binding("curator.mark_known", QKeySequence("S"))


def test_an_external_config_change_repaints_the_keyboard_page(tab):
    """key_bindings is not external-only: a profile switch or an import must repaint the page."""
    tab.update_config(replace(tab.config, key_bindings={"curator.mark_known": "J"}))
    assert _shown(tab, "curator.mark_known") == "J"


def test_reset_to_defaults_keeps_the_key_bindings(tab, monkeypatch):
    tab.update_config(replace(tab.config, key_bindings={"curator.mark_known": "J"}))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    tab._on_reset_to_defaults_clicked()

    assert tab.config.key_bindings == {"curator.mark_known": "J"}
    assert _shown(tab, "curator.mark_known") == "J"


def test_search_jumps_to_a_shortcut_row(tab):
    tab.jump_to_setting("keyboard.curator_mark_known")
    assert tab.current_subtab_key() == "keyboard"

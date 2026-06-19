"""Language change persists via config_changed and does not reload panels."""

from anki_miner.config import create_default_config
from anki_miner.gui.widgets.settings_tab import SettingsTab


def test_language_change_emits_config_changed(qtbot):
    tab = SettingsTab(create_default_config())
    qtbot.addWidget(tab)
    with qtbot.waitSignal(tab.config_changed) as blocker:
        tab._on_language_changed("fr")
    assert blocker.args[0].ui_language == "fr"


def test_ui_language_is_external_only(qtbot):
    # A config diff in only ui_language must NOT trigger a panel reload.
    assert "ui_language" in SettingsTab._EXTERNAL_ONLY_FIELDS

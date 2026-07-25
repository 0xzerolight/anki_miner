"""The UI panel's "Manage Profiles…" request reaches the window unchanged.

The tab only forwards: the dialog is opened by ``MainWindow``, because a profile
switch reloads every panel in this tab from the incoming config.
"""

from anki_miner.config import create_default_config
from anki_miner.gui.widgets.settings_tab import SettingsTab


def test_manage_profiles_request_is_re_emitted(qtbot):
    tab = SettingsTab(create_default_config())
    qtbot.addWidget(tab)
    with qtbot.waitSignal(tab.manage_profiles_requested, timeout=1000):
        tab.ui_panel.manage_profiles_btn.click()

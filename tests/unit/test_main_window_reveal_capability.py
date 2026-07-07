"""MainWindow.reveal_capability / _main_tab_index navigation.

Builds a real MainWindow with heavy startup patched out (same approach as
test_main_window_settings_nav), then inserts stub tab widgets whose class names
match the real tabs so the class-name-based lookup resolves them.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


# Stub tab classes named exactly like the real ones, so _main_tab_index matches.
class SingleEpisodeTab(QWidget): ...


class YouTubeTab(QWidget): ...


class AnalyticsTab(QWidget): ...


class SettingsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.open_ui_subtab = Mock()  # capability marker for _settings_tab_index
        self.open_subtab = Mock()


@pytest.fixture
def window(qtbot, monkeypatch, test_config):
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.tabs.clear()
    win._tabs = {
        "episode": SingleEpisodeTab(),
        "youtube": YouTubeTab(),
        "analytics": AnalyticsTab(),
        "settings": SettingsTab(),
    }
    win.tabs.addTab(win._tabs["episode"], "Episode")
    win.tabs.addTab(win._tabs["youtube"], "YouTube")
    win.tabs.addTab(win._tabs["analytics"], "Analytics")
    win.tabs.addTab(win._tabs["settings"], "Settings")
    yield win
    win.deleteLater()


def test_reveals_a_main_tab_by_key(window):
    window.reveal_capability(CapabilityTarget("youtube"))
    assert window.tabs.currentWidget() is window._tabs["youtube"]


def test_reveals_settings_and_drives_subtab(window):
    window.reveal_capability(CapabilityTarget("settings", "filtering"))
    assert window.tabs.currentWidget() is window._tabs["settings"]
    window._tabs["settings"].open_subtab.assert_called_once_with("filtering")


def test_settings_target_without_subtab_does_not_call_open_subtab(window):
    window.reveal_capability(CapabilityTarget("settings"))
    assert window.tabs.currentWidget() is window._tabs["settings"]
    window._tabs["settings"].open_subtab.assert_not_called()


def test_missing_tab_is_a_silent_noop(window):
    window.tabs.setCurrentIndex(0)
    before = window.tabs.currentWidget()
    # 'audiobook' tab was not inserted in this layout.
    window.reveal_capability(CapabilityTarget("audiobook"))
    assert window.tabs.currentWidget() is before


def test_main_tab_index_unknown_key(window):
    assert window._main_tab_index("nonsense") == -1

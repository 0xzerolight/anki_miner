"""MainWindow.reveal_capability / _main_tab_index navigation.

Builds a real MainWindow with heavy startup patched out (same approach as
test_main_window_settings_nav), then inserts stub tab widgets whose class names
match the real tabs so the class-name-based lookup resolves them.

Also covers the status bar's task strip routing here, because the whole point of
a task carrying a ``CapabilityTarget`` is that choosing it lands on the screen
that owns the run — through the same stable-key lookup, never a tab index.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskSpec


# Stub tab classes named exactly like the real ones, so _main_tab_index matches.
class VideoTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.open_subtab = Mock()


class ReadingTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.open_subtab = Mock()


class AnalyticsTab(QWidget): ...  # deliberately no open_subtab


class SettingsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.open_ui_subtab = Mock()  # capability marker for _settings_tab_index
        self.open_subtab = Mock()


@pytest.fixture
def window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.tabs.clear()
    win._tabs = {
        "video": VideoTab(),
        "reading": ReadingTab(),
        "analytics": AnalyticsTab(),
        "settings": SettingsTab(),
    }
    win.tabs.addTab(win._tabs["video"], "Video")
    win.tabs.addTab(win._tabs["reading"], "Reading")
    win.tabs.addTab(win._tabs["analytics"], "Analytics")
    win.tabs.addTab(win._tabs["settings"], "Settings")
    yield win
    win.task_registry.shutdown()  # stop the one-second ticker before teardown
    win.deleteLater()


def test_reveals_a_main_tab_by_key(window):
    window.reveal_capability(CapabilityTarget("video"))
    assert window.tabs.currentWidget() is window._tabs["video"]


def test_reveals_video_and_drives_subtab(window):
    window.reveal_capability(CapabilityTarget("video", "single"))
    assert window.tabs.currentWidget() is window._tabs["video"]
    window._tabs["video"].open_subtab.assert_called_once_with("single")


def test_reveals_reading_and_drives_subtab(window):
    window.reveal_capability(CapabilityTarget("reading", "novels"))
    assert window.tabs.currentWidget() is window._tabs["reading"]
    window._tabs["reading"].open_subtab.assert_called_once_with("novels")


def test_reveals_settings_and_drives_subtab(window):
    window.reveal_capability(CapabilityTarget("settings", "filtering"))
    assert window.tabs.currentWidget() is window._tabs["settings"]
    window._tabs["settings"].open_subtab.assert_called_once_with("filtering")


def test_settings_target_without_subtab_does_not_call_open_subtab(window):
    window.reveal_capability(CapabilityTarget("settings"))
    assert window.tabs.currentWidget() is window._tabs["settings"]
    window._tabs["settings"].open_subtab.assert_not_called()


def test_subtab_on_widget_without_open_subtab_still_reveals_main_tab(window):
    # A stale subtab on a container that lost open_subtab must not crash;
    # the main tab is still revealed.
    window.reveal_capability(CapabilityTarget("analytics", "nonsense"))
    assert window.tabs.currentWidget() is window._tabs["analytics"]


def test_missing_tab_is_a_silent_noop(window):
    window.tabs.setCurrentIndex(0)
    before = window.tabs.currentWidget()
    # 'audiobook' tab was not inserted in this layout.
    window.reveal_capability(CapabilityTarget("audiobook"))
    assert window.tabs.currentWidget() is before


def test_main_tab_index_unknown_key(window):
    assert window._main_tab_index("nonsense") == -1


def test_the_status_strip_renders_the_window_registry(window):
    window.task_registry.start(
        TaskSpec("dl", "Downloading JMdict", CapabilityTarget("reading", "novels")),
        now=0.0,
    )

    assert "Downloading JMdict" in window.status_bar.task_button.text()


def test_choosing_a_task_reveals_the_screen_that_owns_it(window):
    window.task_registry.start(
        TaskSpec("dl", "Downloading JMdict", CapabilityTarget("reading", "novels")),
        now=0.0,
    )

    window.status_bar.task_activated.emit("dl")

    assert window.tabs.currentWidget() is window._tabs["reading"]
    window._tabs["reading"].open_subtab.assert_called_once_with("novels")


def test_choosing_an_unknown_task_is_a_silent_noop(window):
    window.tabs.setCurrentIndex(0)
    before = window.tabs.currentWidget()

    window.status_bar.task_activated.emit("never-registered")

    assert window.tabs.currentWidget() is before

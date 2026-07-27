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


# ---------------------------------------------------------------------------
# The mini job monitor (D53)
# ---------------------------------------------------------------------------


def test_the_status_bar_opens_the_mini_monitor(window):
    window.task_registry.start(
        TaskSpec("dl", "Downloading JMdict", CapabilityTarget("reading", "novels")),
        now=0.0,
    )

    window.status_bar.mini_monitor_requested.emit()

    assert window._mini_job_monitor is not None
    assert window._mini_job_monitor.isVisible()


def test_reopening_reuses_the_one_window(window):
    window.task_registry.start(
        TaskSpec("dl", "Downloading JMdict", CapabilityTarget("reading", "novels")),
        now=0.0,
    )

    window.open_mini_job_monitor()
    first = window._mini_job_monitor
    first.close()
    window.open_mini_job_monitor()

    assert window._mini_job_monitor is first


def test_it_opens_on_the_run_the_status_strip_is_naming(window):
    window.task_registry.start(
        TaskSpec("a", "Mining Samurai Champloo", CapabilityTarget("video", "single")),
        now=0.0,
    )
    window.task_registry.start(
        TaskSpec("dl", "Downloading JMdict", CapabilityTarget("reading", "novels")),
        now=0.0,
    )

    window.open_mini_job_monitor()

    assert window._mini_job_monitor.watched_run == window.status_bar.displayed_run


def test_show_main_window_brings_the_application_back(window):
    window.open_mini_job_monitor()
    window.showMinimized()

    window._mini_job_monitor.show_main_window_requested.emit()

    assert not window.isMinimized()


def test_the_monitor_is_parented_to_the_window(window):
    """So it is destroyed with the application rather than outliving it."""
    window.open_mini_job_monitor()

    assert window._mini_job_monitor.parent() is window


def test_reopening_keeps_the_job_the_user_picked(window):
    window.task_registry.start(
        TaskSpec("a", "Mining Samurai Champloo", CapabilityTarget("video", "single")),
        now=0.0,
    )
    second = window.task_registry.start(
        TaskSpec("dl", "Downloading JMdict", CapabilityTarget("reading", "novels")),
        now=0.0,
    )
    window.open_mini_job_monitor()
    window._mini_job_monitor.watch("dl", second.run_token)
    window._mini_job_monitor.close()

    window.open_mini_job_monitor()

    assert window._mini_job_monitor.watched_run == ("dl", second.run_token)

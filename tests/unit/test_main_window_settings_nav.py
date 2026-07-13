"""Tests for :class:`MainWindow` settings-tab navigation.

Regression for the "All themes…" / Ctrl+, bug: a stale hardcoded tab index
(``TAB_SETTINGS = 4``) sent the user to the Analytics tab after Analytics was
inserted ahead of Settings. Navigation now locates the Settings tab by
capability, so these tests reproduce the real Analytics-before-Settings layout
and assert we land on Settings, not Analytics.

Builds a real ``MainWindow`` with heavy startup side effects patched out, like
``test_main_window_menu``.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QTabWidget, QWidget


class _SettingsStub(QTabWidget):
    """Stands in for SettingsTab: exposes ``open_ui_subtab`` like the real one."""

    def __init__(self) -> None:
        super().__init__()
        self.open_ui_subtab = Mock()


def _build_window(qtbot, patch_heavy_init, test_config):
    """MainWindow with the real Analytics-before-Settings main-tab layout."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    # Reproduce app.py order: 0..3 placeholders, 4 = Analytics, 5 = Settings.
    window.tabs.clear()
    for label in ("Episode Mining", "Batch Mining", "Deck Builder", "YouTube"):
        window.tabs.addTab(QWidget(), label)
    analytics = QWidget()
    window.tabs.addTab(analytics, "Analytics")
    settings = _SettingsStub()
    window.tabs.addTab(settings, "Settings")
    return window, analytics, settings


@pytest.fixture
def window_tabs(qtbot, patch_heavy_init, test_config):
    window, analytics, settings = _build_window(qtbot, patch_heavy_init, test_config)
    yield window, analytics, settings
    window.deleteLater()


def test_open_theme_settings_lands_on_settings_not_analytics(window_tabs):
    window, analytics, settings = window_tabs
    window._open_theme_settings()
    assert window.tabs.currentWidget() is settings
    assert window.tabs.currentWidget() is not analytics
    settings.open_ui_subtab.assert_called_once()


def test_open_settings_lands_on_settings(window_tabs):
    window, _analytics, settings = window_tabs
    window._open_settings()
    assert window.tabs.currentWidget() is settings


def test_settings_tab_index_requires_capability(qtbot, patch_heavy_init, test_config):
    """A widget without open_ui_subtab is not recognized as the Settings tab."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.tabs.clear()
        window.tabs.addTab(QWidget(), "Analytics")
        plain_settings = QWidget()  # no open_ui_subtab attribute
        window.tabs.addTab(plain_settings, "Settings")
        # The label-fallback was removed to avoid breaking under non-English
        # locales; capability check is the sole lookup path.
        assert window._settings_tab_index() == -1
    finally:
        window.deleteLater()

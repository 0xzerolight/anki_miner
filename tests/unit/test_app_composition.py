"""Tests for the production main-window composition seam."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")


def test_compose_main_window_returns_tabs_without_committing_boot(
    qtbot,
    patch_heavy_init,
    test_config,
):
    patch_heavy_init(test_config)

    from anki_miner.gui.app import ComposedApp, compose_main_window

    composed = compose_main_window(test_config)
    qtbot.addWidget(composed.window)

    assert isinstance(composed, ComposedApp)
    assert composed.window._boot_committed is False
    assert composed.analytics_tab.stats_service is composed.stats_service
    assert composed.window.tabs.widget(4) is composed.analytics_tab
    assert [composed.window.tabs.tabText(index) for index in range(composed.window.tabs.count())] == [
        "Video",
        "Deck Builder",
        "Audio",
        "Reading",
        "Analytics",
        "Tools",
        "Settings",
    ]

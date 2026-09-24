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
    assert composed.window.tabs.widget(3) is composed.analytics_tab
    assert [composed.window.tabs.tabText(index) for index in range(composed.window.tabs.count())] == [
        "Video",
        "Audiobooks",
        "Reading",
        "Analytics",
        "Utilities",
        "Settings",
    ]


def test_backfill_restyle_button_reaches_the_restyle_entry_point(
    qtbot,
    patch_heavy_init,
    test_config,
    monkeypatch,
):
    """Restyle moved off the Tools menu onto Card Backfill (Task 14): the
    button's ``restyle_requested`` signal must actually be wired to
    ``MainWindow.restyle_mined_cards``, not just fire into the void."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui.app import compose_main_window

    patch_heavy_init(test_config)
    composed = compose_main_window(test_config)
    window = composed.window
    qtbot.addWidget(window)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    started: list = []
    monkeypatch.setattr(window.background_tasks, "start_restyle_cards", lambda *a, **k: started.append(a))

    utilities_index = next(i for i in range(window.tabs.count()) if window.tabs.tabText(i) == "Utilities")
    backfill_tab = window.tabs.widget(utilities_index).backfill_tab
    backfill_tab.restyle_button.click()

    assert started, "clicking Restyle cards… did not reach the restyle entry point"

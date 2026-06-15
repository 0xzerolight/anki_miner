"""Regression test for Issue #11: tooltips on Analytics tab table cells.

Long episode/series names are truncated when columns stretch to fit. Without a
tooltip the user has no way to read the full text. Verifies every populated
QTableWidgetItem carries its full text as a tooltip.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import DifficultyEntry, MiningSession, OverallStats

LONG_SERIES = "Tatoeba Saigo ni Hitori Dake Aitsu ga Nokottara Sore wa Boku da"
LONG_EPISODE = f"{LONG_SERIES} - Episode 17 - The Very Long Subtitle That Gets Truncated"


@pytest.fixture
def stats_service() -> MagicMock:
    """Stub StatsService returning a single session and difficulty entry with long names."""
    service = MagicMock()
    service.is_available.return_value = True
    service.get_overall_stats.return_value = OverallStats(
        total_sessions=1,
        total_cards_created=10,
        total_words_encountered=100,
        total_unknown_words=20,
        series_count=1,
    )
    service.get_recent_sessions.return_value = [
        MiningSession(
            id=1,
            series_name=LONG_SERIES,
            episode_name=LONG_EPISODE,
            total_words=200,
            unknown_words=30,
            cards_created=12,
            mined_at=datetime(2026, 5, 16, 14, 30),
        )
    ]
    service.get_series_difficulty.return_value = [
        DifficultyEntry(
            series_name=LONG_SERIES,
            total_words=200,
            unknown_words=30,
            difficulty_score=0.15,
        )
    ]
    service.get_milestones.return_value = []
    return service


@pytest.fixture
def tab(stats_service: MagicMock, qtbot):
    widget = AnalyticsTab(stats_service)
    qtbot.addWidget(widget)
    widget.refresh_data()
    yield widget
    widget.deleteLater()


def test_sessions_table_items_have_tooltips(tab: AnalyticsTab) -> None:
    """Every cell in the sessions table exposes its full text via tooltip (Issue #11)."""
    table = tab.sessions_table
    assert table.rowCount() == 1
    for col in range(table.columnCount()):
        item = table.item(0, col)
        assert item is not None, f"missing item at column {col}"
        assert item.toolTip() == item.text(), (
            f"sessions table column {col} tooltip mismatch: " f"tooltip={item.toolTip()!r} text={item.text()!r}"
        )


def test_sessions_episode_tooltip_contains_full_name(tab: AnalyticsTab) -> None:
    """The episode column tooltip must contain the full untruncated episode name."""
    table = tab.sessions_table
    episode_item = table.item(0, 2)
    assert episode_item is not None
    assert episode_item.toolTip() == LONG_EPISODE


def test_difficulty_table_items_have_tooltips(tab: AnalyticsTab) -> None:
    """Every cell in the difficulty table exposes its full text via tooltip (Issue #11)."""
    table = tab.difficulty_table
    assert table.rowCount() == 1
    for col in range(table.columnCount()):
        item = table.item(0, col)
        assert item is not None, f"missing item at column {col}"
        assert item.toolTip() == item.text(), (
            f"difficulty table column {col} tooltip mismatch: " f"tooltip={item.toolTip()!r} text={item.text()!r}"
        )


def test_difficulty_series_tooltip_contains_full_name(tab: AnalyticsTab) -> None:
    """The series column tooltip must contain the full untruncated series name."""
    table = tab.difficulty_table
    series_item = table.item(0, 1)
    assert series_item is not None
    assert series_item.toolTip() == LONG_SERIES

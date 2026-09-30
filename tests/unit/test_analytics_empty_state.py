"""E09 and D19: Analytics without a Refresh button, fake checkboxes or an average tile.

Refresh is gone: a finished run marks the page stale. Milestones state "Reached"
instead of a disabled checkbox. With no sessions the page is one line, not four
cards of zeros. The average tile was the first tile divided by the second.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QCheckBox, QLabel, QProgressBar

from anki_miner.gui.widgets import analytics_tab as analytics_tab_module
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import Milestone, MilestoneKind, OverallStats


def _sync_run_off_thread(parent, work, on_done, on_error=None, *, error_prefix=""):
    on_done(work())
    return None


def _service(sessions: int) -> MagicMock:
    service = MagicMock()
    service.is_available.return_value = True
    service.get_overall_stats.return_value = OverallStats(
        total_sessions=sessions, total_cards_created=sessions * 4, series_count=1 if sessions else 0
    )
    service.get_recent_sessions.return_value = []
    service.get_series_difficulty.return_value = []
    service.get_milestones.return_value = []
    return service


@pytest.fixture
def make_tab(qtbot, monkeypatch):
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)

    def _make(sessions: int) -> AnalyticsTab:
        tab = AnalyticsTab(_service(sessions))
        qtbot.addWidget(tab)
        tab.refresh_data(force=True)
        return tab

    return _make


def test_there_is_no_refresh_button(make_tab):
    tab = make_tab(3)

    assert not hasattr(tab, "refresh_button")
    assert [label for label in tab.findChildren(QLabel) if label.text() == "Refresh"] == []


def test_there_is_no_average_tile(make_tab):
    tab = make_tab(3)

    assert not hasattr(tab, "card_avg_cards")


def test_no_sessions_is_one_line(make_tab):
    tab = make_tab(0)

    assert not tab.empty_state_label.isHidden()
    assert tab.empty_state_label.text() == "No mining yet — your statistics appear here after your first run."
    assert tab.card_total_cards.isHidden() or not tab.card_total_cards.isVisibleTo(tab)
    assert tab.sessions_table.isVisibleTo(tab) is False
    assert tab.reset_button.isHidden()


def test_sessions_bring_the_sections_back(make_tab):
    tab = make_tab(3)

    assert tab.empty_state_label.isHidden()
    assert tab.card_total_cards.isVisibleTo(tab)
    assert not tab.reset_button.isHidden()


def test_a_finished_run_refreshes_a_visible_page(make_tab, qtbot):
    tab = make_tab(3)
    tab.show()
    qtbot.waitExposed(tab)
    tab.stats_service.get_overall_stats.reset_mock()

    tab.mark_stale()

    assert tab.stats_service.get_overall_stats.call_count == 1


def test_a_finished_run_leaves_a_hidden_page_for_its_next_visit(make_tab):
    tab = make_tab(3)
    tab.stats_service.get_overall_stats.reset_mock()

    tab.mark_stale()

    assert tab.stats_service.get_overall_stats.call_count == 0
    assert tab._last_refresh is None


def test_a_reached_milestone_says_so_instead_of_a_checkbox(make_tab):
    tab = make_tab(3)

    row = tab._create_milestone_widget(
        Milestone(kind=MilestoneKind.CARDS, threshold=50, current_value=60, achieved=True)
    )

    assert row.findChildren(QCheckBox) == []
    assert row.findChildren(QProgressBar) == []
    assert "Reached" in [label.text() for label in row.findChildren(QLabel)]


def test_an_open_milestone_keeps_its_bar_and_no_checkbox(make_tab):
    tab = make_tab(3)

    row = tab._create_milestone_widget(
        Milestone(kind=MilestoneKind.CARDS, threshold=50, current_value=10, achieved=False)
    )

    assert row.findChildren(QCheckBox) == []
    assert row.findChild(QProgressBar) is not None


def test_main_window_marks_analytics_stale_after_a_result(qtbot, patch_heavy_init, test_config):
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.models.processing import ProcessingResult

    patch_heavy_init(test_config)
    window = MainWindow()
    qtbot.addWidget(window)

    class AnalyticsTab(QLabel):  # matched by class name, like the real tab
        def __init__(self) -> None:
            super().__init__()
            self.stale = 0

        def mark_stale(self) -> None:
            self.stale += 1

    fake = AnalyticsTab()
    window.tabs.addTab(fake, "Analytics")

    window._on_processing_result(ProcessingResult(total_words_found=1, new_words_found=1, cards_created=1))

    assert fake.stale == 1
    window.deleteLater()

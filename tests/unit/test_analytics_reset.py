"""Analytics -> Reset Statistics: the confirm, the wipe, and the re-arm rules.

The delete runs off the GUI thread like every other stats query on this tab, the
emptied tab is the only success receipt, and a failure is a screen issue rather
than a modal (D24). Reset and refresh are serialised against each other so a
refresh cannot render a pre-delete snapshot after the delete has landed.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.gui.widgets import analytics_tab as analytics_tab_module
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import DifficultyEntry, OverallStats

_MAIN_THREAD_ID = threading.get_ident()


def _stats(sessions: int = 0) -> OverallStats:
    return OverallStats(
        total_sessions=sessions,
        total_cards_created=sessions * 5,
        total_words_encountered=sessions * 100,
        total_unknown_words=sessions * 10,
        series_count=1 if sessions else 0,
    )


def _difficulty() -> DifficultyEntry:
    return DifficultyEntry(
        series_name="Show",
        total_words=100,
        unknown_words=10,
        unique_words=80,
        difficulty_score=0.1,
    )


def _make_service(sessions: int = 3) -> MagicMock:
    service = MagicMock()
    service.is_available.return_value = True
    service.get_overall_stats.return_value = _stats(sessions)
    service.get_recent_sessions.return_value = []
    service.get_series_difficulty.return_value = [_difficulty()] if sessions else []
    service.get_milestones.return_value = []
    service.reset.return_value = sessions
    return service


def _sync_run_off_thread(parent, work, on_done, on_error=None, *, error_prefix=""):
    """Drop-in for run_off_thread that runs the work inline on the calling thread."""
    try:
        result = work()
    except Exception as exc:  # noqa: BLE001 — mirror the worker error path
        if on_error is not None:
            on_error(f"{error_prefix}{exc}")
        return None
    on_done(result)
    return None


@pytest.fixture
def message_boxes(monkeypatch):
    """Capture QMessageBox usage; question answers Yes unless overridden."""
    boxes: dict = {"questions": [], "answer": QMessageBox.StandardButton.Yes}
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: boxes["questions"].append((a[1], a[2])) or boxes["answer"]
    )
    return boxes


@pytest.fixture
def tab(qtbot, monkeypatch):
    """A tab whose off-thread dispatch runs inline, already refreshed once."""
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
    service = _make_service()
    widget = AnalyticsTab(service)
    qtbot.addWidget(widget)
    widget.refresh_data(force=True)
    return widget


class TestArming:
    """The button is only clickable when there is something to delete."""

    def test_disabled_on_construction(self, qtbot):
        widget = AnalyticsTab(_make_service())
        qtbot.addWidget(widget)

        assert widget.reset_button.isEnabled() is False

    def test_enabled_after_a_bundle_with_sessions(self, tab):
        assert tab.reset_button.isEnabled() is True

    def test_still_disabled_after_an_empty_bundle(self, qtbot, monkeypatch):
        monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
        widget = AnalyticsTab(_make_service(sessions=0))
        qtbot.addWidget(widget)
        widget.refresh_data(force=True)

        assert widget.reset_button.isEnabled() is False

    def test_enabled_by_difficulty_rows_alone(self, qtbot, monkeypatch):
        """Difficulty rows with no sessions still count as data worth wiping."""
        monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
        service = _make_service(sessions=0)
        service.get_series_difficulty.return_value = [_difficulty()]
        widget = AnalyticsTab(service)
        qtbot.addWidget(widget)
        widget.refresh_data(force=True)

        assert widget.reset_button.isEnabled() is True

    def test_a_failed_refresh_leaves_it_disabled(self, qtbot, monkeypatch):
        """After a failed read the tab's contents are unknown; do not offer a wipe."""
        monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
        service = _make_service()
        widget = AnalyticsTab(service)
        qtbot.addWidget(widget)
        service.get_overall_stats.side_effect = RuntimeError("db gone")
        widget.refresh_data(force=True)

        assert widget.reset_button.isEnabled() is False


class TestConfirmation:
    def test_declining_does_not_touch_the_database(self, tab, message_boxes):
        message_boxes["answer"] = QMessageBox.StandardButton.No
        tab._on_reset_clicked()

        assert len(message_boxes["questions"]) == 1
        tab.stats_service.reset.assert_not_called()

    def test_declining_leaves_the_button_armed(self, tab, message_boxes):
        message_boxes["answer"] = QMessageBox.StandardButton.No
        tab._on_reset_clicked()

        assert tab.reset_button.isEnabled() is True
        assert tab._reset_in_flight is False

    def test_the_prompt_says_what_survives(self, tab, message_boxes):
        tab._on_reset_clicked()
        _title, body = message_boxes["questions"][0]

        assert "cannot be undone" in body
        assert "known words" in body


class TestReset:
    def test_accepting_wipes_and_re_reads(self, tab, message_boxes):
        tab.stats_service.get_overall_stats.return_value = _stats(0)
        tab.stats_service.get_series_difficulty.return_value = []
        tab.stats_service.get_overall_stats.reset_mock()

        tab._on_reset_clicked()

        tab.stats_service.reset.assert_called_once()
        # The forced re-read is what repaints the zeros.
        assert tab.stats_service.get_overall_stats.call_count == 1
        assert tab.card_total_sessions.value_label.text() == "0"

    def test_the_button_disarms_itself_on_success(self, tab, message_boxes):
        tab.stats_service.get_overall_stats.return_value = _stats(0)
        tab.stats_service.get_series_difficulty.return_value = []

        tab._on_reset_clicked()

        assert tab.reset_button.isEnabled() is False
        assert tab.refresh_button.isEnabled() is True
        assert tab._reset_in_flight is False

    def test_the_delete_runs_off_the_gui_thread(self, qtbot, message_boxes):
        """No monkeypatched dispatch here: the real worker must own the delete."""
        service = _make_service()
        seen: list[int] = []
        service.reset.side_effect = lambda: seen.append(threading.get_ident()) or 3

        widget = AnalyticsTab(service)
        qtbot.addWidget(widget)
        widget.refresh_data(force=True)
        qtbot.waitUntil(lambda: widget._refresh_in_flight is False, timeout=3000)

        widget._on_reset_clicked()
        qtbot.waitUntil(lambda: widget._reset_in_flight is False, timeout=3000)

        assert seen and _MAIN_THREAD_ID not in seen


class TestFailure:
    def test_failure_is_a_screen_issue_not_a_modal(self, tab, message_boxes, monkeypatch):
        shown: list[object] = []
        monkeypatch.setattr(type(tab), "show_screen_issue", lambda self, issue, **k: shown.append(issue))
        tab.stats_service.reset.side_effect = RuntimeError("database is locked")

        tab._on_reset_clicked()

        assert len(shown) == 1
        assert "could not be reset" in shown[0].summary
        assert "database is locked" in shown[0].details

    def test_failure_re_arms_both_buttons(self, tab, message_boxes):
        tab.stats_service.reset.side_effect = RuntimeError("database is locked")

        tab._on_reset_clicked()

        assert tab.reset_button.isEnabled() is True
        assert tab.refresh_button.isEnabled() is True
        assert tab._reset_in_flight is False


class TestSerialisation:
    def test_refresh_is_a_no_op_while_a_reset_runs(self, tab):
        tab.stats_service.get_overall_stats.reset_mock()
        tab._reset_in_flight = True

        tab.refresh_data(force=True)

        tab.stats_service.get_overall_stats.assert_not_called()

    def test_an_in_flight_refresh_disarms_the_reset_button(self, tab):
        """The two operations can never overlap in either direction."""
        seen: list[bool] = []
        tab.stats_service.get_overall_stats.side_effect = lambda: seen.append(tab.reset_button.isEnabled()) or _stats(3)

        tab.refresh_data(force=True)

        assert seen == [False]

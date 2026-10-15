"""AI-16: Analytics history rows can show or delete the run's cards."""

from __future__ import annotations

import inspect
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.widgets import analytics_tab as analytics_tab_module
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import MiningSession, OverallStats, RunReceipt

_POST = "anki_miner.services.anki_service.post_action"


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


def _session(
    session_id: int, note_count: int, day: int, *, cards: int | None = None, deleted: int = 0
) -> MiningSession:
    return MiningSession(
        id=session_id,
        series_name="Show",
        episode_name=f"ep{session_id:02d}",
        cards_created=note_count if cards is None else cards,
        note_count=note_count,
        deleted_count=deleted,
        mined_at=datetime(2026, 10, day, 12, 0),
    )


def _service(sessions: list[MiningSession]) -> MagicMock:
    service = MagicMock()
    service.is_available.return_value = True
    service.language = "ja"
    service.get_overall_stats.return_value = OverallStats(
        total_sessions=len(sessions), total_cards_created=sum(s.cards_created for s in sessions), series_count=1
    )
    service.get_recent_sessions.return_value = sessions
    service.get_series_difficulty.return_value = []
    service.get_milestones.return_value = []
    return service


@pytest.fixture
def make_tab(qtbot, monkeypatch, test_config):
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)

    def _make(sessions: list[MiningSession], *, with_config: bool = True) -> AnalyticsTab:
        tab = AnalyticsTab(_service(sessions), config_provider=(lambda: test_config) if with_config else None)
        qtbot.addWidget(tab)
        tab.refresh_data(force=True)
        return tab

    return _make


@pytest.fixture
def message_boxes(monkeypatch):
    """Capture QMessageBox.question; it answers Yes unless a test overrides it."""
    boxes: dict = {"questions": [], "answer": QMessageBox.StandardButton.Yes}
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: boxes["questions"].append((a[1], a[2])) or boxes["answer"]
    )
    return boxes


def _select(tab: AnalyticsTab, session_id: int) -> None:
    table = tab.sessions_table
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.data(Qt.ItemDataRole.UserRole) == session_id:
            table.selectRow(row)
            return
    raise AssertionError(f"no row for session {session_id}")


def test_nothing_selected_disables_both_actions(make_tab):
    tab = make_tab([_session(1, 2, 15)])

    assert not tab.show_in_anki_button.isEnabled()
    assert not tab.delete_run_button.isEnabled()
    assert tab.delete_run_button.toolTip() == "Select one run in the table."


def test_a_run_with_a_card_list_enables_both(make_tab):
    tab = make_tab([_session(1, 2, 15), _session(2, 0, 14)])

    _select(tab, 1)

    assert tab.show_in_anki_button.isEnabled()
    assert tab.delete_run_button.isEnabled()
    assert tab.delete_run_button.toolTip() == ""


def test_an_older_run_is_disabled_with_a_reason(make_tab):
    """R8: runs recorded before this update have no note ids."""
    tab = make_tab([_session(1, 2, 15), _session(2, 0, 14)])

    _select(tab, 2)

    assert not tab.delete_run_button.isEnabled()
    assert "no card list" in tab.delete_run_button.toolTip()


def test_a_run_whose_cards_were_deleted_is_marked(make_tab):
    """Ruling C5: the row keeps its count, says "cards deleted" with how many, and stays disarmed."""
    tab = make_tab([_session(1, 0, 15, cards=3, deleted=3), _session(2, 2, 14)])

    _select(tab, 1)

    [row] = {index.row() for index in tab.sessions_table.selectedIndexes()}
    assert tab.sessions_table.item(row, 5).text() == "3 (cards deleted: 3)"
    assert not tab.delete_run_button.isEnabled()
    assert not tab.show_in_anki_button.isEnabled()
    assert tab.delete_run_button.toolTip() == "This run's cards were deleted: 3."


def test_without_a_config_the_actions_stay_off(make_tab):
    tab = make_tab([_session(1, 2, 15)], with_config=False)

    _select(tab, 1)

    assert not tab.delete_run_button.isEnabled()
    assert tab.run_actions_row.isHidden()


def test_the_app_hands_analytics_the_live_config():
    from anki_miner.gui import app

    assert "config_provider=window.get_config" in inspect.getsource(app.compose_main_window)


def test_show_in_anki_browses_the_runs_notes(make_tab, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(_POST, lambda *a, **k: calls.append((a[1], k.get("params"))) or None)
    tab = make_tab([_session(1, 2, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11, 12), ())
    _select(tab, 1)

    tab.show_in_anki_button.click()

    assert calls == [("guiBrowse", {"query": "nid:11,12"})]
    assert tab.issue_banner().current_issue() is None


def test_show_in_anki_with_anki_closed_is_a_banner(make_tab, monkeypatch):
    def _down(*_a, **_k):
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")

    monkeypatch.setattr(_POST, _down)
    tab = make_tab([_session(1, 2, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11,), ())
    _select(tab, 1)

    tab.show_in_anki_button.click()

    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Anki Miner couldn't open this run's cards in Anki."
    assert "Cannot connect" in issue.details

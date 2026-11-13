"""AI-16: Analytics history rows can show or delete the run's cards."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
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


class _FakeAnki:
    """AnkiConnect at the post_action seam."""

    def __init__(self, existing=(), reviewed=()) -> None:
        self.existing = set(existing)
        self.reviewed = set(reviewed)
        self.deleted: list[list[int]] = []

    def __call__(self, *args, **kwargs):
        action = args[1]
        params = kwargs.get("params") or {}
        if action == "findNotes":
            query = params["query"]
            ids = {int(part) for part in query.split()[0].removeprefix("nid:").split(",")}
            return sorted(ids & (self.reviewed if "-is:new" in query else self.existing))
        if action == "deleteNotes":
            self.deleted.append(list(params["notes"]))
            return None
        raise AssertionError(action)


def test_the_confirm_counts_the_cards_and_the_reviewed_ones(make_tab, monkeypatch, message_boxes):
    fake = _FakeAnki(existing=(11, 13), reviewed=(13,))
    monkeypatch.setattr(_POST, fake)
    tab = make_tab([_session(1, 3, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11, 12, 13), ())
    _select(tab, 1)

    tab.delete_run_button.click()

    [(title, text)] = message_boxes["questions"]
    assert title == "Delete This Run's Cards"
    assert "still in Anki: 2" in text
    assert "Reviewed at least once: 1" in text
    assert fake.deleted == [[11, 13]]
    tab.stats_service.forget_run_notes.assert_called_once_with(1)
    assert tab.run_action_note.text() == "Cards deleted from Anki: 2."


def test_declining_deletes_nothing(make_tab, monkeypatch, message_boxes):
    fake = _FakeAnki(existing=(11,), reviewed=())
    monkeypatch.setattr(_POST, fake)
    message_boxes["answer"] = QMessageBox.StandardButton.No
    tab = make_tab([_session(1, 1, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11,), ())
    _select(tab, 1)

    tab.delete_run_button.click()

    assert fake.deleted == []
    tab.stats_service.forget_run_notes.assert_not_called()
    assert tab.delete_run_button.isEnabled()


def test_all_cards_already_gone_forgets_the_list_without_asking(make_tab, monkeypatch, message_boxes):
    """Review Focus 4: every note was deleted in Anki; nothing to confirm, the list is dropped."""
    monkeypatch.setattr(_POST, _FakeAnki(existing=(), reviewed=()))
    tab = make_tab([_session(1, 2, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11, 12), ())
    _select(tab, 1)

    tab.delete_run_button.click()

    assert message_boxes["questions"] == []
    tab.stats_service.forget_run_notes.assert_called_once_with(1)
    assert tab.run_action_note.text() == "None of this run's cards are in Anki any more."


def test_an_unreachable_anki_on_delete_is_a_banner(make_tab, monkeypatch, message_boxes):
    """Review Focus 4: Anki closed is a banner, and the row stays armed for a retry."""

    def _down(*_a, **_k):
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")

    monkeypatch.setattr(_POST, _down)
    tab = make_tab([_session(1, 2, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11,), ())
    _select(tab, 1)

    tab.delete_run_button.click()

    issue = tab.issue_banner().current_issue()
    assert issue is not None and issue.summary == "This run's cards could not be deleted."
    assert message_boxes["questions"] == []
    assert tab.delete_run_button.isEnabled()


def test_a_delete_refreshes_the_page(make_tab, monkeypatch, message_boxes):
    monkeypatch.setattr(_POST, _FakeAnki(existing=(11,), reviewed=()))
    tab = make_tab([_session(1, 1, 15)])
    tab.stats_service.get_run_receipt.return_value = RunReceipt(1, "ja", (11,), ())
    _select(tab, 1)
    before = tab.stats_service.get_overall_stats.call_count

    tab.delete_run_button.click()

    assert tab.stats_service.get_overall_stats.call_count == before + 1


def _named(session_id: int, name: str, mined_at: datetime) -> MiningSession:
    return MiningSession(
        id=session_id,
        series_name=name,
        episode_name="ep",
        cards_created=1,
        note_count=1,
        mined_at=mined_at,
    )


def _show_without_scan(tab: AnalyticsTab, qtbot) -> None:
    # The known-words scan reaches Anki; these tests are about the history table.
    tab._maybe_refresh_knowledge = lambda: None  # type: ignore[method-assign]
    tab.show()
    qtbot.waitExposed(tab)


def test_a_refresh_keeps_the_selection_on_the_same_run(make_tab, qtbot, monkeypatch, message_boxes):
    """U8b-001: a run finishing between select and click must not retarget the delete."""
    fake = _FakeAnki(existing=(1001, 1002, 1003, 1004, 2000))
    monkeypatch.setattr(_POST, fake)
    sessions = [_named(i, f"OLD-{i}", datetime(2026, 10, 10 + i, 12, 0)) for i in (4, 3, 2, 1)]
    tab = make_tab(sessions)
    tab.stats_service.get_run_receipt.side_effect = lambda sid: RunReceipt(
        sid, "ja", (2000,) if sid == 5 else (1000 + sid,), ()
    )
    _show_without_scan(tab, qtbot)
    _select(tab, 3)
    assert tab._selected_session().series_name == "OLD-3"

    tab.stats_service.get_recent_sessions.return_value = [
        _named(5, "JUST-MINED", datetime(2026, 10, 20, 12, 0)),
        *sessions,
    ]
    tab.mark_stale()
    assert tab.sessions_table.rowCount() == 5

    assert tab._selected_session().series_name == "OLD-3"
    tab.delete_run_button.click()
    tab.stats_service.get_run_receipt.assert_called_with(3)
    assert fake.deleted == [[1003]]


def test_a_refresh_that_drops_the_selected_run_clears_the_selection(make_tab, qtbot):
    now = datetime.now()
    recent = _named(1, "RECENT", now - timedelta(days=1))
    old = _named(2, "OLD", now - timedelta(days=60))
    tab = make_tab([recent, old])
    _show_without_scan(tab, qtbot)
    _select(tab, 2)
    assert tab.delete_run_button.isEnabled()

    tab.history_range_combo.setCurrentIndex(tab.history_range_combo.findData(7))

    assert tab.sessions_table.rowCount() == 1
    assert tab._selected_session() is None
    assert not tab.delete_run_button.isEnabled()

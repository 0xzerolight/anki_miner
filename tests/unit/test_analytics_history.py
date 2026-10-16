"""ST-02: Analytics lists every run, cut by a range and grouped by period."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets import analytics_tab as analytics_tab_module
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab, _AnalyticsBundle
from anki_miner.models.stats import MiningSession, OverallStats


def _sync_run_off_thread(parent, work, on_done, on_error=None, *, error_prefix=""):
    try:
        result = work()
    except Exception as exc:  # noqa: BLE001 — mirror the worker error path
        if on_error is not None:
            on_error(f"{error_prefix}{exc}")
        return None
    on_done(result)
    return None


def _run(session_id: int, when: datetime, cards: int) -> MiningSession:
    return MiningSession(
        id=session_id,
        series_name="Show",
        episode_name=f"ep{session_id:02d}",
        total_words=100,
        unknown_words=10,
        cards_created=cards,
        mined_at=when,
    )


@pytest.fixture
def tab(qtbot) -> AnalyticsTab:
    service = MagicMock()
    service.is_available.return_value = False  # showEvent must not start a worker
    widget = AnalyticsTab(service)
    qtbot.addWidget(widget)
    return widget


def _render(tab: AnalyticsTab, sessions: list[MiningSession]) -> None:
    tab._apply_bundle(
        _AnalyticsBundle(
            stats=OverallStats(
                total_sessions=len(sessions), total_cards_created=sum(s.cards_created for s in sessions)
            ),
            sessions=sessions,
            difficulties=[],
            milestones=[],
        )
    )


def _choose(combo, data) -> None:
    combo.setCurrentIndex(combo.findData(data))


def test_the_page_reads_the_whole_history(qtbot, monkeypatch):
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
    service = MagicMock()
    service.is_available.return_value = True
    service.get_overall_stats.return_value = OverallStats()
    service.get_recent_sessions.return_value = []
    service.get_series_difficulty.return_value = []
    service.get_milestones.return_value = []
    widget = AnalyticsTab(service)
    qtbot.addWidget(widget)

    widget.refresh_data(force=True)

    service.get_recent_sessions.assert_called_once_with(limit=None)


def test_every_run_is_listed_by_default(tab):
    _render(tab, [_run(i, datetime(2026, 1, 1) + timedelta(days=i), 1) for i in range(1, 26)])

    assert tab.sessions_table.rowCount() == 25
    assert tab.history_groups_table.isHidden()
    assert not tab.history_chart.isHidden()


def test_grouping_by_week_swaps_the_tables(tab):
    _render(
        tab,
        [
            _run(1, datetime(2026, 10, 12, 9), 1),
            _run(2, datetime(2026, 10, 13, 9), 2),
            _run(3, datetime(2026, 9, 1, 9), 4),
        ],
    )

    _choose(tab.history_group_combo, "week")

    assert tab.sessions_table.isHidden()
    assert tab.run_actions_row.isHidden()
    table = tab.history_groups_table
    assert not table.isHidden()
    rows = {table.item(r, 0).text(): table.item(r, 4).text() for r in range(table.rowCount())}
    assert rows == {"2026-W42": "3", "2026-W36": "4"}


def test_a_range_hides_older_runs(tab):
    now = datetime.now()
    _render(tab, [_run(1, now - timedelta(days=1), 1), _run(2, now - timedelta(days=60), 2)])

    _choose(tab.history_range_combo, 30)

    assert tab.sessions_table.rowCount() == 1


def test_an_empty_range_says_so(tab):
    _render(tab, [_run(1, datetime.now() - timedelta(days=60), 2)])

    _choose(tab.history_range_combo, 7)

    assert not tab.sessions_empty_label.isHidden()
    assert tab.sessions_empty_label.text() == "No runs in this period."
    assert tab.history_chart.isHidden()


def test_the_chart_counts_cards_per_day_oldest_first(tab):
    _render(
        tab,
        [
            _run(1, datetime(2026, 10, 15, 20), 2),
            _run(2, datetime(2026, 10, 15, 9), 3),
            _run(3, datetime(2026, 10, 14, 9), 4),
        ],
    )

    assert tab.history_chart.bars() == [("2026-10-14", 4), ("2026-10-15", 5)]


def test_the_chart_follows_the_grouping(tab):
    _render(tab, [_run(1, datetime(2026, 10, 15), 2), _run(2, datetime(2026, 9, 15), 3)])

    _choose(tab.history_group_combo, "month")

    assert tab.history_chart.bars() == [("2026-09", 3), ("2026-10", 2)]


def test_export_writes_the_runs_on_show(tab, monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
    target = tmp_path / "history.csv"
    monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(str(target)))
    run = _run(1, datetime(2026, 10, 15, 9, 30), 5)
    run.series_name = "葬送のフリーレン"
    _render(tab, [run])

    tab.export_history_button.click()

    assert target.read_text(encoding="utf-8-sig").splitlines() == [
        "Date,Series,Episode,Words,New Words,Cards",
        "2026-10-15 09:30,葬送のフリーレン,ep01,100,10,5",
    ]
    assert tab.history_export_note.text() == "History saved to history.csv."


def test_export_of_a_grouping_writes_the_periods(tab, monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
    target = tmp_path / "weeks.csv"
    monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(str(target)))
    _render(tab, [_run(1, datetime(2026, 10, 12, 9), 5)])
    _choose(tab.history_group_combo, "week")

    tab.export_history_button.click()

    assert target.read_text(encoding="utf-8-sig").splitlines() == [
        "Period,Sessions,Words,New Words,Cards",
        "2026-W42,1,100,10,5",
    ]


def test_a_failed_export_is_a_banner(tab, monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
    target = tmp_path / "missing-folder" / "history.csv"
    monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(str(target)))
    _render(tab, [_run(1, datetime(2026, 10, 15), 1)])

    tab.export_history_button.click()

    issue = tab.issue_banner().current_issue()
    assert issue is not None and issue.summary == "The history could not be exported."


def test_nothing_on_show_disables_export(tab):
    _render(tab, [_run(1, datetime.now() - timedelta(days=60), 1)])

    _choose(tab.history_range_combo, 7)

    assert not tab.export_history_button.isEnabled()

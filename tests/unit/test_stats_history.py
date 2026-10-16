"""ST-02: the whole history, cut by date and grouped by day, ISO week or month."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from anki_miner.models.stats import HistoryGrouping, MiningSession
from anki_miner.services.stats_service import export_csv, group_sessions, sessions_since


def _run(when: datetime, cards: int, words: int = 100, unknown: int = 10) -> MiningSession:
    return MiningSession(
        series_name="S", episode_name="e", total_words=words, unknown_words=unknown, cards_created=cards, mined_at=when
    )


def test_sessions_since_keeps_runs_on_or_after_the_cutoff():
    runs = [_run(datetime(2026, 10, 15), 1), _run(datetime(2026, 10, 10), 2), _run(datetime(2026, 10, 1), 3)]

    assert sessions_since(runs, datetime(2026, 10, 10)) == runs[:2]
    assert sessions_since(runs, None) == runs


def test_grouping_by_day_sums_each_day_newest_first():
    groups = group_sessions(
        [_run(datetime(2026, 10, 15, 9), 1), _run(datetime(2026, 10, 15, 20), 2), _run(datetime(2026, 10, 14, 9), 4)],
        HistoryGrouping.DAY,
    )

    assert [(g.key, g.start, g.sessions, g.cards_created, g.total_words, g.unknown_words) for g in groups] == [
        ("2026-10-15", date(2026, 10, 15), 2, 3, 200, 20),
        ("2026-10-14", date(2026, 10, 14), 1, 4, 100, 10),
    ]


def test_grouping_by_week_uses_iso_weeks():
    # 2026-10-11 is a Sunday (ISO week 41, from Monday 10-05); 2026-10-12 a Monday (week 42).
    groups = group_sessions([_run(datetime(2026, 10, 12), 1), _run(datetime(2026, 10, 11), 2)], HistoryGrouping.WEEK)

    assert [(g.key, g.start, g.cards_created) for g in groups] == [
        ("2026-W42", date(2026, 10, 12), 1),
        ("2026-W41", date(2026, 10, 5), 2),
    ]


def test_grouping_by_month():
    groups = group_sessions([_run(datetime(2026, 10, 1), 1), _run(datetime(2026, 9, 30), 2)], HistoryGrouping.MONTH)

    assert [(g.key, g.start) for g in groups] == [("2026-10", date(2026, 10, 1)), ("2026-09", date(2026, 9, 1))]


def test_each_run_is_not_a_grouping():
    with pytest.raises(ValueError):
        group_sessions([], HistoryGrouping.RUN)


def test_export_csv_writes_utf8_with_a_header(tmp_path):
    path = tmp_path / "history.csv"

    count = export_csv(path, ["Date", "Series", "Cards"], [["2026-10-15", "葬送のフリーレン", 5]])

    assert count == 1
    assert path.read_text(encoding="utf-8-sig").splitlines() == ["Date,Series,Cards", "2026-10-15,葬送のフリーレン,5"]
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")

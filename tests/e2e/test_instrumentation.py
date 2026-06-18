"""Tests for the E2E state-instrumentation + divergence-detection layer.

The bulk are PURE-UNIT detector tests: they build synthetic ``list[StateSnapshot]``
with hand-chosen values (no Qt / Anki / disk) and assert that ``detect_divergence``
distinguishes SUSPECT growth (widgets/threads/temp files/RSS — should be stable
across mining sessions) from EXPECTED growth (deck/history/stats rows — should
grow as cards are created), per ``instrumentation``'s documented heuristics.

A single light integration test exercises ``capture_snapshot`` against a
``tmp_path`` home (some DBs present, some absent) with ``gateway=None`` to prove
it never raises and degrades missing inputs to ``0`` / ``None``.

Qt-only (no Anki / no ffmpeg) → default suite, no pytest marker.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import cast

from tests.e2e.instrumentation import (
    MONOTONIC_MIN_GAPS,
    RSS_SLOPE_BYTES_PER_SESSION,
    DivergenceReport,
    StateSnapshot,
    capture_snapshot,
    detect_divergence,
    diff_snapshots,
)

# Roughly one session's worth of legitimate RSS noise, comfortably under the
# RSS_SLOPE threshold so a flat-with-jitter series stays PASS.
_RSS_NOISE = RSS_SLOPE_BYTES_PER_SESSION // 4


def _snap(
    *,
    index: int = 0,
    top_level_widgets: int = 1,
    python_threads: int = 4,
    qthread_pool_active: int = 0,
    rss_bytes: int = 100_000_000,
    sqlite_rows: dict[str, int] | None = None,
    temp_files: int = 0,
    anki_test_deck_count: int | None = None,
    label: str = "",
) -> StateSnapshot:
    """Build a synthetic snapshot, defaulting every field to a stable baseline."""
    return StateSnapshot(
        index=index,
        top_level_widgets=top_level_widgets,
        python_threads=python_threads,
        qthread_pool_active=qthread_pool_active,
        rss_bytes=rss_bytes,
        sqlite_rows=sqlite_rows if sqlite_rows is not None else {},
        temp_files=temp_files,
        anki_test_deck_count=anki_test_deck_count,
        label=label,
    )


def _stable_series(n: int = 5) -> list[StateSnapshot]:
    """A baseline series where nothing leaks (only mild RSS jitter)."""
    return [_snap(index=i, rss_bytes=100_000_000 + (i % 2) * _RSS_NOISE) for i in range(n)]


# --------------------------------------------------------------------------
# detect_divergence: suspect (leak) metrics
# --------------------------------------------------------------------------


def test_baseline_stable_series_is_pass():
    report = detect_divergence(_stable_series())
    assert isinstance(report, DivergenceReport)
    assert report.verdict == "PASS"
    assert report.flags == []


def test_monotonic_temp_files_is_flagged_fail():
    snaps = [_snap(index=i, temp_files=i) for i in range(5)]  # 0,1,2,3,4
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("temp_files" in f for f in report.flags)
    assert report.suspect_deltas["temp_files"] == 4  # last - first


def test_monotonic_threads_is_flagged():
    snaps = [_snap(index=i, python_threads=4 + i) for i in range(5)]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("python_threads" in f for f in report.flags)


def test_monotonic_widgets_is_flagged():
    snaps = [_snap(index=i, top_level_widgets=1 + i) for i in range(5)]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("top_level_widgets" in f for f in report.flags)


def test_monotonic_qthread_pool_is_flagged():
    snaps = [_snap(index=i, qthread_pool_active=i) for i in range(5)]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("qthread_pool_active" in f for f in report.flags)


def test_two_sessions_positive_delta_not_flagged():
    # 2 sessions → 1 gap → below MONOTONIC_MIN_GAPS threshold → no FAIL.
    # Suspect delta is still recorded.
    snaps = [_snap(index=0, temp_files=0), _snap(index=1, temp_files=1)]
    report = detect_divergence(snaps)
    assert report.verdict == "PASS"
    assert report.flags == []
    assert report.suspect_deltas["temp_files"] == 1  # delta still recorded


def test_three_sessions_genuine_leak_is_flagged():
    # 3 sessions → 2 gaps = MONOTONIC_MIN_GAPS → fraction rule applies → FAIL.
    snaps = [_snap(index=i, temp_files=i) for i in range(3)]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("temp_files" in f for f in report.flags)
    assert report.suspect_deltas["temp_files"] == 2  # 0 -> 2


def test_four_sessions_genuine_monotonic_leak_is_flagged():
    # 4 sessions → 3 gaps → fraction rule catches a real leak (all 3 positive).
    snaps = [_snap(index=i, temp_files=i) for i in range(4)]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("temp_files" in f for f in report.flags)
    assert report.suspect_deltas["temp_files"] == 3


def test_monotonic_min_gaps_constant_is_two():
    # Regression guard: the threshold value itself must not drift.
    assert MONOTONIC_MIN_GAPS == 2


def test_single_blip_in_temp_files_is_not_flagged():
    # One step up then back down — not monotonic, tolerated as noise.
    snaps = [
        _snap(index=0, temp_files=0),
        _snap(index=1, temp_files=2),
        _snap(index=2, temp_files=0),
        _snap(index=3, temp_files=0),
        _snap(index=4, temp_files=0),
    ]
    report = detect_divergence(snaps)
    assert report.verdict == "PASS"
    assert report.flags == []


def test_growth_with_one_noise_dip_still_flagged():
    # Positive delta in >= N-1 of N gaps (one dip) still counts as a leak.
    snaps = [
        _snap(index=0, temp_files=0),
        _snap(index=1, temp_files=1),
        _snap(index=2, temp_files=2),
        _snap(index=3, temp_files=1),  # the dip
        _snap(index=4, temp_files=3),
    ]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("temp_files" in f for f in report.flags)


# --------------------------------------------------------------------------
# detect_divergence: EXPECTED growers must NOT be flagged (false-positive guard)
# --------------------------------------------------------------------------


def test_expected_deck_growth_not_flagged():
    snaps = [_snap(index=i, anki_test_deck_count=i * 10) for i in range(5)]
    report = detect_divergence(snaps)
    assert report.verdict == "PASS"
    assert report.flags == []


def test_expected_sqlite_growth_not_flagged():
    # history.db / stats.db / known_words.db SHOULD grow as cards are mined.
    snaps = [
        _snap(
            index=i,
            sqlite_rows={
                "history.db": i * 3,
                "stats.db": i * 7,
                "known_words.db": i * 5,
            },
            anki_test_deck_count=i * 10,
        )
        for i in range(5)
    ]
    report = detect_divergence(snaps)
    assert report.verdict == "PASS"
    assert report.flags == []


def test_mixed_expected_growth_and_suspect_leak_flags_only_suspect():
    snaps = [
        _snap(
            index=i,
            temp_files=i,  # suspect leak
            anki_test_deck_count=i * 10,  # expected growth
            sqlite_rows={"history.db": i * 3},  # expected growth
        )
        for i in range(5)
    ]
    report = detect_divergence(snaps)
    assert report.verdict == "FAIL"
    assert any("temp_files" in f for f in report.flags)
    # No expected-grower metric name should appear in a leak flag.
    joined = " ".join(report.flags)
    assert "anki_test_deck_count" not in joined
    assert "history.db" not in joined


# --------------------------------------------------------------------------
# detect_divergence: RSS slope
# --------------------------------------------------------------------------


def test_rss_slope_above_threshold_is_flagged():
    # +2x the threshold per session, sustained.
    step = RSS_SLOPE_BYTES_PER_SESSION * 2
    snaps = [_snap(index=i, rss_bytes=100_000_000 + i * step) for i in range(5)]
    report = detect_divergence(snaps)
    assert report.verdict in ("WARN", "FAIL")
    assert any("rss" in f.lower() for f in report.flags)


def test_rss_slope_below_threshold_not_flagged():
    step = RSS_SLOPE_BYTES_PER_SESSION // 5  # well under threshold
    snaps = [_snap(index=i, rss_bytes=100_000_000 + i * step) for i in range(5)]
    report = detect_divergence(snaps)
    assert all("rss" not in f.lower() for f in report.flags)


def test_rss_noise_around_flat_not_flagged():
    # Up-and-down jitter, no sustained trend.
    rss = [100_000_000, 100_000_000 + _RSS_NOISE, 100_000_000, 100_000_000 + _RSS_NOISE, 100_000_000]
    snaps = [_snap(index=i, rss_bytes=v) for i, v in enumerate(rss)]
    report = detect_divergence(snaps)
    assert all("rss" not in f.lower() for f in report.flags)


# --------------------------------------------------------------------------
# detect_divergence: cards -> 0 divergence (investigate, NOT fail)
# --------------------------------------------------------------------------


def test_cards_dropping_to_zero_after_positive_is_investigate_not_fail():
    snaps = _stable_series(4)
    report = detect_divergence(snaps, cards_created=[5, 3, 0, 0])
    # Investigate-level: surfaced as a flag, but not a hard FAIL on its own.
    assert any("investigate" in f.lower() or "cards" in f.lower() for f in report.flags)
    assert report.verdict in ("PASS", "WARN")  # not FAIL purely from cards->0


def test_cards_all_positive_no_investigate_flag():
    snaps = _stable_series(4)
    report = detect_divergence(snaps, cards_created=[5, 3, 4, 2])
    assert all("cards" not in f.lower() for f in report.flags)
    assert report.verdict == "PASS"


def test_cards_zero_throughout_not_investigate():
    # Never created any cards -> no "dropped from positive to zero" signal.
    snaps = _stable_series(4)
    report = detect_divergence(snaps, cards_created=[0, 0, 0, 0])
    assert all("cards" not in f.lower() for f in report.flags)


# --------------------------------------------------------------------------
# detect_divergence: edge cases
# --------------------------------------------------------------------------


def test_too_few_snapshots_is_pass_no_flags():
    assert detect_divergence([]).verdict == "PASS"
    assert detect_divergence([_snap()]).verdict == "PASS"


def test_report_is_dataclass_serializable():
    report = detect_divergence([_snap(index=i, temp_files=i) for i in range(5)])
    d = dataclasses.asdict(report)
    assert set(d) >= {"verdict", "flags", "suspect_deltas", "expected_deltas"}
    assert isinstance(d["flags"], list)


# --------------------------------------------------------------------------
# diff_snapshots
# --------------------------------------------------------------------------


def test_diff_snapshots_computes_int_deltas():
    pre = _snap(top_level_widgets=1, python_threads=4, qthread_pool_active=0, rss_bytes=100, temp_files=2)
    post = _snap(top_level_widgets=3, python_threads=6, qthread_pool_active=1, rss_bytes=250, temp_files=5)
    d = diff_snapshots(pre, post)
    assert d["top_level_widgets"] == 2
    assert d["python_threads"] == 2
    assert d["qthread_pool_active"] == 1
    assert d["rss_bytes"] == 150
    assert d["temp_files"] == 3


def test_diff_snapshots_sqlite_per_key():
    pre = _snap(sqlite_rows={"history.db": 10, "stats.db": 5})
    post = _snap(sqlite_rows={"history.db": 14, "stats.db": 5, "known_words.db": 2})
    d = diff_snapshots(pre, post)
    rows = cast("dict[str, int]", d["sqlite_rows"])
    assert rows["history.db"] == 4
    assert rows["stats.db"] == 0
    assert rows["known_words.db"] == 2  # absent in pre -> treated as 0


def test_diff_snapshots_deck_count_none_handling():
    # Both None -> None delta.
    assert (
        diff_snapshots(_snap(anki_test_deck_count=None), _snap(anki_test_deck_count=None))["anki_test_deck_count"]
        is None
    )
    # Both present -> int delta.
    assert diff_snapshots(_snap(anki_test_deck_count=2), _snap(anki_test_deck_count=9))["anki_test_deck_count"] == 7
    # One None -> None (can't diff).
    assert (
        diff_snapshots(_snap(anki_test_deck_count=None), _snap(anki_test_deck_count=9))["anki_test_deck_count"] is None
    )


# --------------------------------------------------------------------------
# capture_snapshot: light integration (tmp_path home, no Anki)
# --------------------------------------------------------------------------


def _make_sqlite_with_rows(path: Path, rows: int) -> None:
    """Create a tiny sqlite DB with one table holding ``rows`` rows."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def test_capture_snapshot_populates_without_raising(qapp, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # Present: known_words.db (3 rows). Absent: history.db, stats.db.
    _make_sqlite_with_rows(home / "known_words.db", 3)

    media_temp = tmp_path / "media_temp"
    media_temp.mkdir()
    (media_temp / "a.mp3").write_bytes(b"x")
    (media_temp / "b.png").write_bytes(b"y")
    # A nested run-temp subdir the processor might use.
    (media_temp / "anki_miner_temp").mkdir()
    (media_temp / "anki_miner_temp" / "c.jpg").write_bytes(b"z")

    snap = capture_snapshot(test_home=home, media_temp_folder=media_temp, gateway=None)

    assert isinstance(snap, StateSnapshot)
    # In-process metrics are real ints (this process / qapp present).
    assert isinstance(snap.top_level_widgets, int)
    assert isinstance(snap.python_threads, int) and snap.python_threads >= 1
    assert isinstance(snap.qthread_pool_active, int)
    assert isinstance(snap.rss_bytes, int) and snap.rss_bytes > 0
    # sqlite: present DB counted, absent DBs degrade to 0.
    assert snap.sqlite_rows["known_words.db"] == 3
    assert snap.sqlite_rows["history.db"] == 0
    assert snap.sqlite_rows["stats.db"] == 0
    # temp files counted recursively (2 + 1 nested).
    assert snap.temp_files == 3
    # No gateway -> deck count is None (Anki is NOT a hard dependency).
    assert snap.anki_test_deck_count is None


def test_capture_snapshot_missing_dirs_degrade_to_zero(qapp, tmp_path):
    # Nothing exists under home; media_temp dir absent entirely.
    home = tmp_path / "ghost_home"  # not created
    snap = capture_snapshot(test_home=home, media_temp_folder=tmp_path / "nope", gateway=None)
    assert snap.sqlite_rows == {"known_words.db": 0, "history.db": 0, "stats.db": 0}
    assert snap.temp_files == 0
    assert snap.anki_test_deck_count is None
    # Still a valid snapshot with real in-process numbers.
    assert isinstance(snap.rss_bytes, int) and snap.rss_bytes > 0


def test_capture_snapshot_default_media_temp_under_home(qapp, tmp_path):
    # When media_temp_folder is omitted, capture falls back to <home>/media_temp.
    home = tmp_path / "home2"
    (home / "media_temp").mkdir(parents=True)
    (home / "media_temp" / "leftover.tmp").write_bytes(b"x")
    snap = capture_snapshot(test_home=home, gateway=None)
    assert snap.temp_files == 1


# --------------------------------------------------------------------------
# detect_divergence: mode="crossprocess" — in-process metrics must NOT drive verdict
# --------------------------------------------------------------------------


def test_crossprocess_widget_growth_does_not_fail():
    """Growing top_level_widgets must NOT FAIL in crossprocess mode."""
    snaps = [_snap(index=i, top_level_widgets=1 + i) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    assert report.verdict != "FAIL"
    assert all("top_level_widgets" not in f for f in report.flags)


def test_crossprocess_thread_growth_does_not_fail():
    """Growing python_threads must NOT FAIL in crossprocess mode."""
    snaps = [_snap(index=i, python_threads=4 + i) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    assert report.verdict != "FAIL"
    assert all("python_threads" not in f for f in report.flags)


def test_crossprocess_qthread_pool_growth_does_not_fail():
    """Growing qthread_pool_active must NOT FAIL in crossprocess mode."""
    snaps = [_snap(index=i, qthread_pool_active=i) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    assert report.verdict != "FAIL"
    assert all("qthread_pool_active" not in f for f in report.flags)


def test_crossprocess_rss_slope_does_not_warn():
    """High RSS slope must NOT WARN in crossprocess mode."""
    step = RSS_SLOPE_BYTES_PER_SESSION * 2
    snaps = [_snap(index=i, rss_bytes=100_000_000 + i * step) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    assert all("rss" not in f.lower() for f in report.flags)


def test_crossprocess_temp_files_growth_still_fails():
    """Growing temp_files DOES still FAIL in crossprocess mode (disk is authoritative)."""
    snaps = [_snap(index=i, temp_files=i) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    assert report.verdict == "FAIL"
    assert any("temp_files" in f for f in report.flags)


def test_crossprocess_in_process_metrics_recorded_as_context():
    """In crossprocess mode, growing in-process metrics are still in suspect_deltas."""
    snaps = [_snap(index=i, top_level_widgets=1 + i, python_threads=4 + i) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    # Deltas present for context even though they don't drive the verdict.
    assert "top_level_widgets" in report.suspect_deltas
    assert "python_threads" in report.suspect_deltas
    assert "rss_bytes" in report.suspect_deltas
    assert "rss_slope_bytes_per_session" in report.suspect_deltas


def test_inprocess_widget_growth_still_fails():
    """Sanity: mode="inprocess" (default) still FAILs on widget growth."""
    snaps = [_snap(index=i, top_level_widgets=1 + i) for i in range(5)]
    report = detect_divergence(snaps, mode="inprocess")
    assert report.verdict == "FAIL"
    assert any("top_level_widgets" in f for f in report.flags)


def test_crossprocess_stable_everything_is_pass():
    """A fully stable series stays PASS in crossprocess mode."""
    report = detect_divergence(_stable_series(), mode="crossprocess")
    assert report.verdict == "PASS"
    assert report.flags == []


def test_crossprocess_widget_leak_plus_temp_files_leak_fails_on_temp():
    """Both in-process and disk leaks: crossprocess verdict is FAIL from temp_files only."""
    snaps = [_snap(index=i, top_level_widgets=1 + i, temp_files=i) for i in range(5)]
    report = detect_divergence(snaps, mode="crossprocess")
    assert report.verdict == "FAIL"
    # temp_files drives the flag, NOT top_level_widgets.
    assert any("temp_files" in f for f in report.flags)
    assert all("top_level_widgets" not in f for f in report.flags)

"""State instrumentation + divergence detection for the E2E soak runner.

The soak runner mines the same episode several sessions in a row to surface a
bug that "only appears after several mining sessions." Between sessions it calls
:func:`capture_snapshot` to record process/disk/deck metrics, then feeds the
series to :func:`detect_divergence`, which produces a triage :class:`DivergenceReport`
(``verdict`` + human-readable ``flags`` + per-metric deltas) for the agent to read
out of ``report.json``.

The whole module is **pure Python** (Qt/psutil/sqlite reads only) — no AnkiConnect,
ffmpeg, or network is required; the deck count is the one optional, Anki-dependent
metric and degrades to ``None`` when no gateway is supplied or Anki is unreachable.

Expected vs. suspect growth (the core distinction)
--------------------------------------------------
Mining LEGITIMATELY grows some metrics every session — that is the feature
working, not a bug:

* ``anki_test_deck_count`` — each session creates cards.
* ``history.db`` / ``stats.db`` rows — every run appends history + analytics.
* ``known_words.db`` rows — known-words accumulate as cards are made.

These are the :data:`EXPECTED_GROWERS`. Their growth is REPORTED (in
``expected_deltas``) but never flagged as a problem.

The bug we hunt is unbounded growth of things that should be STABLE across
sessions — leaked widgets, leaked threads, an ever-growing thread pool, temp
files that are never cleaned, or a steadily-climbing RSS. Those are the
:data:`SUSPECT_METRICS`; sustained per-session growth in any of them is flagged.

Heuristics (deliberately SIMPLE — a triage aid, not statistics)
---------------------------------------------------------------
* **Monotonic growth of a suspect count.** A metric "grows monotonically" when
  it has a positive delta in at least :data:`MONOTONIC_MIN_FRACTION` of the
  session-to-session gaps AND a positive net delta end-to-end. The fraction (not
  *every* gap) tolerates a single noise dip while still catching a real leak.
  → ``FAIL``.
* **RSS slope.** Least-squares slope of ``rss_bytes`` over the session index. If
  it exceeds :data:`RSS_SLOPE_BYTES_PER_SESSION`, RSS is climbing steadily. RSS
  is noisier (allocator, caches) than a widget/thread count, so it is a softer
  signal → ``WARN`` (does not by itself FAIL).
* **cards → 0 after positive.** If the (optional) ``cards_created`` series goes
  from a positive count to ``0`` in a later session, that *can* be legitimate
  (known-words subtraction eventually mines nothing), so it is surfaced as an
  *investigate* flag, never a hard FAIL — per the harness plan.

All thresholds are named constants below; tune them there.

Parent vs child snapshot semantics (in-process vs cross-process mode)
----------------------------------------------------------------------
:func:`capture_snapshot` is always called in the PARENT (runner) process. In
``inprocess`` mode the parent also hosts the Qt application and the pipeline
workers, so the in-process metrics (``top_level_widgets``, ``python_threads``,
``qthread_pool_active``, ``rss_bytes``) reflect the pipeline and are a
meaningful series across sessions.

In ``crossprocess`` mode each session runs in a SEPARATE subprocess. The parent
calls ``capture_snapshot`` between sessions to record disk state, but the
in-process numbers (widgets, threads, RSS) are the PARENT's idle numbers, not
the child's — they are NOT a meaningful series across sessions and must not drive
the verdict. Only disk metrics (``temp_files``; sqlite row counts stay as
expected-growers context) are authoritative across processes.

Pass ``mode="crossprocess"`` to :func:`detect_divergence` (or via
``_assemble_report``) so the in-process metrics and RSS slope are still recorded
in ``suspect_deltas`` for context but never generate a LEAK/WARN flag.

Input / output contract (for the runner's serializer)
------------------------------------------------------
:func:`detect_divergence` takes the per-session ``list[StateSnapshot]`` (the
snapshot captured *after* each session is the natural choice), an optional
parallel ``cards_created: list[int]``, and an optional ``mode`` (default
``"inprocess"``). It returns a :class:`DivergenceReport` dataclass; both it and
:class:`StateSnapshot` use only JSON-friendly fields so ``dataclasses.asdict``
round-trips straight into ``report.json``.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import psutil  # type: ignore[import-untyped]

if TYPE_CHECKING:  # avoid a hard import cycle / Anki dependency at import time
    from tests.e2e.anki_gateway import AnkiGateway

__all__ = [
    "EXPECTED_GROWERS",
    "MONOTONIC_MIN_FRACTION",
    "RSS_SLOPE_BYTES_PER_SESSION",
    "SUSPECT_METRICS",
    "DivergenceReport",
    "StateSnapshot",
    "capture_snapshot",
    "detect_divergence",
    "diff_snapshots",
]

Verdict = Literal["PASS", "WARN", "FAIL"]

# The three SQLite databases the pipeline writes under the test home. Names match
# the project's data-paths table (and app_config's pinned *_db_path fields).
_SQLITE_DB_NAMES = ("known_words.db", "history.db", "stats.db")

#: Scalar metrics that SHOULD stay roughly constant across mining sessions.
#: Sustained growth in any of these is a leak suspect and is flagged.
SUSPECT_METRICS = ("top_level_widgets", "python_threads", "qthread_pool_active", "temp_files")

#: SQLite DB names that legitimately grow each session (keys into
#: ``StateSnapshot.sqlite_rows``). ``known_words.db`` is included because the
#: known-words cache is additive across sessions. Single source of truth so the
#: per-DB context loop in ``detect_divergence`` can't drift from EXPECTED_GROWERS.
_EXPECTED_DB_NAMES = ("history.db", "stats.db", "known_words.db")

#: Metrics that legitimately grow as mining creates cards. Their growth is
#: reported but never flagged.
EXPECTED_GROWERS = ("anki_test_deck_count", *_EXPECTED_DB_NAMES)

#: In-process metrics that reflect the calling process, NOT a child session's
#: state. In ``crossprocess`` mode, snapshots are taken by the parent between
#: child sessions, so these numbers are the parent's idle numbers — they must
#: NOT drive the verdict (still recorded in suspect_deltas for context).
_INPROCESS_ONLY_METRICS: frozenset[str] = frozenset(("top_level_widgets", "python_threads", "qthread_pool_active"))

#: Fraction of session-to-session gaps that must show a positive delta for a
#: suspect metric to count as "monotonically growing". 0.75 means "positive in
#: at least 3 of 4 gaps" — catches a real leak while tolerating one noise dip.
MONOTONIC_MIN_FRACTION = 0.75

#: RSS slope (bytes gained per session, least-squares) above which RSS is judged
#: to be climbing steadily. ~5 MB/session sustained over a soak run is a lot.
RSS_SLOPE_BYTES_PER_SESSION = 5 * 1024 * 1024


@dataclass(frozen=True)
class StateSnapshot:
    """One session's process/disk/deck metrics (all JSON-friendly fields).

    In-process metrics (``top_level_widgets``, ``python_threads``,
    ``qthread_pool_active``, ``rss_bytes``) reflect the process that called
    :func:`capture_snapshot`. In the cross-process soak loop the PARENT calls
    capture (to read disk + deck), so those in-process numbers are the parent's
    and the runner should ignore them for cross-process leak detection; they are
    meaningful for an in-process loop.
    """

    #: Session ordinal for reporting (0-based). Carried so a report can name the
    #: session a flag first appeared in without a parallel index list.
    index: int = 0
    #: ``len(QApplication.topLevelWidgets())``; ``0`` when there is no QApplication.
    top_level_widgets: int = 0
    #: ``threading.active_count()`` for the calling process.
    python_threads: int = 0
    #: ``QThreadPool.globalInstance().activeThreadCount()``; ``0`` without a QApplication.
    qthread_pool_active: int = 0
    #: ``psutil.Process().memory_info().rss`` (resident set size, bytes).
    rss_bytes: int = 0
    #: Per-DB TOTAL row count keyed by db file name (e.g. ``"history.db"``) —
    #: summed across every user table found via ``sqlite_master``. A missing DB
    #: file contributes ``0``. (Summed, not per-table, because the detector only
    #: needs a growth signal per database, not per table.)
    sqlite_rows: dict[str, int] = field(default_factory=dict)
    #: Count of files (recursive) under the configured media-temp folder.
    temp_files: int = 0
    #: Cards in the test deck, or ``None`` when no gateway was supplied / Anki was
    #: unreachable. OPTIONAL — Anki is never a hard dependency of capture.
    anki_test_deck_count: int | None = None
    #: Optional free-text label for reporting (e.g. ``"post-session-3"``).
    label: str = ""


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------


def _count_qt_state() -> tuple[int, int]:
    """Return ``(top_level_widgets, qthread_pool_active)``, degrading to ``0``.

    Imports Qt lazily and guards on a live QApplication so capture works in a
    process that never built one (and never raises on a Qt hiccup).
    """
    try:
        from PyQt6.QtCore import QThreadPool
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return 0, 0
        widgets = len(QApplication.topLevelWidgets())
        pool = QThreadPool.globalInstance()
        active = pool.activeThreadCount() if pool is not None else 0
        return widgets, active
    except Exception:
        return 0, 0


def _count_sqlite_rows(db_path: Path) -> int:
    """Sum row counts across every user table in a sqlite DB (read-only).

    Table names are introspected from ``sqlite_master`` so a schema change does
    not break this. A missing file, a non-sqlite file, or any sqlite error all
    degrade to ``0`` — capture must never raise on bad on-disk state.
    """
    if not db_path.is_file():
        return 0
    conn: sqlite3.Connection | None = None
    try:
        # Open read-only via URI so we never create or lock the DB for writes.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        total = 0
        for name in names:
            # Table names come from sqlite_master (not user input); quote to be safe.
            (count,) = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()
            total += int(count)
        return total
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _count_temp_files(folder: Path) -> int:
    """Recursively count files under ``folder``; a missing dir degrades to ``0``.

    Counts files only (not directories) anywhere beneath ``folder`` so any
    run-temp subdir the processor creates (e.g. ``anki_miner_temp/``) is included.
    """
    if not folder.is_dir():
        return 0
    try:
        return sum(1 for p in folder.rglob("*") if p.is_file())
    except Exception:
        return 0


def _deck_count(gateway: AnkiGateway | None) -> int | None:
    """Return the test deck's card count, or ``None`` if unavailable.

    Anki is OPTIONAL: no gateway, or any error reaching it, yields ``None`` rather
    than raising, so capture works with Anki down.
    """
    if gateway is None:
        return None
    try:
        return gateway.deck_card_count()
    except Exception:
        return None


def capture_snapshot(
    *,
    test_home: Path,
    media_temp_folder: Path | None = None,
    gateway: AnkiGateway | None = None,
    index: int = 0,
    label: str = "",
) -> StateSnapshot:
    """Capture one :class:`StateSnapshot`, filling what it can and never raising.

    Args:
        test_home: Isolated home dir holding the pipeline's SQLite DBs
            (``known_words.db`` / ``history.db`` / ``stats.db``).
        media_temp_folder: The processor's media-temp folder. Defaults to
            ``test_home / "media_temp"`` (matching ``app_config.build_app_config``)
            when omitted.
        gateway: Optional :class:`AnkiGateway` for the deck count. ``None`` (or an
            unreachable Anki) leaves ``anki_test_deck_count`` as ``None``.
        index: Session ordinal recorded on the snapshot.
        label: Optional free-text label recorded on the snapshot.

    Returns:
        A populated :class:`StateSnapshot`. Missing DBs/dirs degrade to ``0``; an
        absent QApplication degrades the Qt metrics to ``0``; absent/unreachable
        Anki degrades the deck count to ``None``.
    """
    test_home = Path(test_home)
    media_temp = Path(media_temp_folder) if media_temp_folder is not None else test_home / "media_temp"

    widgets, pool_active = _count_qt_state()
    sqlite_rows = {name: _count_sqlite_rows(test_home / name) for name in _SQLITE_DB_NAMES}

    return StateSnapshot(
        index=index,
        top_level_widgets=widgets,
        python_threads=threading.active_count(),
        qthread_pool_active=pool_active,
        rss_bytes=psutil.Process().memory_info().rss,
        sqlite_rows=sqlite_rows,
        temp_files=_count_temp_files(media_temp),
        anki_test_deck_count=_deck_count(gateway),
        label=label,
    )


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def diff_snapshots(pre: StateSnapshot, post: StateSnapshot) -> dict[str, object]:
    """Return the per-metric ``post - pre`` delta.

    Scalar int metrics subtract directly. ``sqlite_rows`` diffs per db-name key
    (a key absent in ``pre`` is treated as ``0``). ``anki_test_deck_count`` yields
    an int delta only when BOTH snapshots have a count; if either is ``None`` the
    delta is ``None`` (can't diff a missing reading).
    """
    keys = set(pre.sqlite_rows) | set(post.sqlite_rows)
    sqlite_delta = {k: post.sqlite_rows.get(k, 0) - pre.sqlite_rows.get(k, 0) for k in keys}

    if pre.anki_test_deck_count is None or post.anki_test_deck_count is None:
        deck_delta: int | None = None
    else:
        deck_delta = post.anki_test_deck_count - pre.anki_test_deck_count

    return {
        "top_level_widgets": post.top_level_widgets - pre.top_level_widgets,
        "python_threads": post.python_threads - pre.python_threads,
        "qthread_pool_active": post.qthread_pool_active - pre.qthread_pool_active,
        "rss_bytes": post.rss_bytes - pre.rss_bytes,
        "temp_files": post.temp_files - pre.temp_files,
        "sqlite_rows": sqlite_delta,
        "anki_test_deck_count": deck_delta,
    }


# --------------------------------------------------------------------------
# detect
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DivergenceReport:
    """Triage result fed into ``report.json`` (all JSON-friendly fields).

    Attributes:
        verdict: ``"PASS"`` (nothing flagged), ``"WARN"`` (soft signal — RSS slope
            and/or cards→0 investigate), or ``"FAIL"`` (a suspect count leaked).
        flags: Human-readable one-line strings, one per detected signal.
        suspect_deltas: End-to-end (last - first) delta for each
            :data:`SUSPECT_METRICS` entry plus ``rss_bytes`` and the RSS slope.
        expected_deltas: End-to-end delta for each :data:`EXPECTED_GROWERS` entry,
            reported for context (growth here is normal, never flagged).
    """

    verdict: Verdict = "PASS"
    flags: list[str] = field(default_factory=list)
    suspect_deltas: dict[str, int] = field(default_factory=dict)
    expected_deltas: dict[str, object] = field(default_factory=dict)


def _series(snapshots: list[StateSnapshot], attr: str) -> list[int]:
    """Pull one scalar attribute across the snapshot series."""
    return [int(getattr(s, attr)) for s in snapshots]


def _grows_monotonically(values: list[int]) -> bool:
    """True if ``values`` climbs across the series (tolerating one noise dip).

    Rule (documented, simple): a positive delta in at least
    :data:`MONOTONIC_MIN_FRACTION` of the consecutive gaps AND a positive net
    delta end-to-end. With < 2 points there are no gaps, so it's never growth.
    """
    if len(values) < 2:
        return False
    # values[1:] is intentionally one shorter -> non-strict zip pairs each
    # value with its successor (the last value has no successor).
    gaps = [b - a for a, b in zip(values, values[1:], strict=False)]
    positive = sum(1 for d in gaps if d > 0)
    net_positive = values[-1] > values[0]
    return net_positive and positive >= MONOTONIC_MIN_FRACTION * len(gaps)


def _rss_slope(values: list[int]) -> float:
    """Least-squares slope (bytes/session) of ``values`` over index 0..n-1.

    A plain linear fit; with < 2 points the slope is ``0.0``.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values, strict=True))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def _sqlite_series(snapshots: list[StateSnapshot], db_name: str) -> list[int]:
    """Per-session row count for one db name (missing key -> 0)."""
    return [int(s.sqlite_rows.get(db_name, 0)) for s in snapshots]


def detect_divergence(
    snapshots: list[StateSnapshot],
    *,
    cards_created: list[int] | None = None,
    mode: Literal["inprocess", "crossprocess"] = "inprocess",
) -> DivergenceReport:
    """Flag session-over-session divergence in the snapshot series.

    Args:
        snapshots: Per-session snapshots in order (typically the snapshot captured
            *after* each session). Fewer than 2 snapshots → an empty ``PASS``
            report (no trend to judge).
        cards_created: Optional parallel per-session card counts. A drop from a
            positive count to ``0`` later in the run is surfaced as an
            *investigate* flag (WARN, not FAIL) — it can be legitimate
            known-words accumulation.
        mode: ``"inprocess"`` (default) evaluates all :data:`SUSPECT_METRICS`
            and the RSS slope to drive the verdict.  ``"crossprocess"`` skips
            the in-process metrics (``top_level_widgets``, ``python_threads``,
            ``qthread_pool_active``) and the RSS slope when deciding the verdict
            — they are still recorded in ``suspect_deltas`` for context but
            never generate a LEAK/WARN flag, because those numbers are the
            parent's idle numbers rather than a meaningful per-session series.
            Only ``temp_files`` (disk) drives FAIL in cross-process mode.

    Returns:
        A :class:`DivergenceReport` whose ``verdict`` is the worst signal found:
        a leaked SUSPECT count → ``FAIL``; only an RSS slope and/or cards→0 →
        ``WARN``; nothing → ``PASS``.
    """
    report_flags: list[str] = []
    suspect_deltas: dict[str, int] = {}
    expected_deltas: dict[str, object] = {}
    fail = False
    warn = False

    # Always record expected-grower deltas for context (never flagged).
    if len(snapshots) >= 2:
        first, last = snapshots[0], snapshots[-1]
        if first.anki_test_deck_count is not None and last.anki_test_deck_count is not None:
            expected_deltas["anki_test_deck_count"] = last.anki_test_deck_count - first.anki_test_deck_count
        else:
            expected_deltas["anki_test_deck_count"] = None
        for db_name in _EXPECTED_DB_NAMES:
            vals = _sqlite_series(snapshots, db_name)
            expected_deltas[db_name] = vals[-1] - vals[0]

    if len(snapshots) < 2:
        return DivergenceReport(verdict="PASS", flags=[], suspect_deltas={}, expected_deltas=expected_deltas)

    # --- suspect scalar counts: monotonic growth => leak (FAIL) ----------
    for metric in SUSPECT_METRICS:
        vals = _series(snapshots, metric)
        suspect_deltas[metric] = vals[-1] - vals[0]
        # In crossprocess mode, in-process metrics are recorded for context but
        # never drive the verdict (they reflect the parent's idle state, not the
        # child sessions).
        if mode == "crossprocess" and metric in _INPROCESS_ONLY_METRICS:
            continue
        if _grows_monotonically(vals):
            fail = True
            report_flags.append(
                f"LEAK: {metric} grew monotonically {vals[0]} -> {vals[-1]} "
                f"across {len(snapshots)} sessions (should be stable)"
            )

    # --- RSS slope: steady climb => soft signal (WARN) -------------------
    rss_vals = _series(snapshots, "rss_bytes")
    slope = _rss_slope(rss_vals)
    suspect_deltas["rss_bytes"] = rss_vals[-1] - rss_vals[0]
    suspect_deltas["rss_slope_bytes_per_session"] = int(slope)
    # In crossprocess mode, RSS is the parent's idle RSS, not a leak signal.
    if mode == "inprocess" and slope > RSS_SLOPE_BYTES_PER_SESSION:
        warn = True
        report_flags.append(
            f"RSS climbing ~{slope / (1024 * 1024):.1f} MB/session "
            f"(threshold {RSS_SLOPE_BYTES_PER_SESSION / (1024 * 1024):.0f} MB) — possible memory leak"
        )

    # --- cards -> 0 after positive: investigate (WARN, never FAIL) -------
    if cards_created:
        seen_positive = False
        for i, n in enumerate(cards_created):
            if n > 0:
                seen_positive = True
            elif n == 0 and seen_positive:
                warn = True
                report_flags.append(
                    f"INVESTIGATE: cards_created dropped to 0 at session {i} after earlier "
                    f"positive sessions — may be legitimate known-words accumulation"
                )
                break

    verdict: Verdict = "FAIL" if fail else ("WARN" if warn else "PASS")
    return DivergenceReport(
        verdict=verdict,
        flags=report_flags,
        suspect_deltas=suspect_deltas,
        expected_deltas=expected_deltas,
    )

"""Soak runner for the E2E GUI test harness.

Mines the same episode several sessions in a row and instruments process/disk/
deck state between sessions to surface both multi-session accumulation/leak bugs
AND GUI-consistency/integration bugs (widget state, mined word sets, cancel/error
paths, known-words accumulation) that unit tests cannot reproduce. It composes
the prior harness building blocks
(``E2EConfig``, ``build_app_config``, ``EpisodeTabDriver``, ``AutoCurationResponder``,
the ``instrumentation`` snapshot/diff/divergence functions, ``AnkiGateway``,
``RunDir``, and the home-isolation primitives) — it is glue plus the two loop
shapes plus report assembly, no new pipeline logic.

Two loop shapes
---------------
``run_inprocess_soak`` builds ONE :class:`EpisodeTabDriver` (one QApplication /
one tab) and mines ``sessions`` times reusing it. Reusing the tab is what
exposes widget/worker/QThread/RSS leaks: the tab's own ``_start_processing``
calls ``_teardown_previous_run`` between mines, and between sessions we ALSO
call ``driver.teardown()`` and drain Qt deferred-deletes (the
``tests/conftest.py::_drain_qt_deletes`` idiom) so anything that survives is a
genuine leak. The in-process snapshots are the meaningful ones for leak hunting.

``run_crossprocess_soak`` spawns a FRESH subprocess per session
(``python -m tests.e2e.soak --one-session ...``). Each child inherits
``ANKI_MINER_HOME`` from the env BEFORE importing anki_miner, so it is a clean
process. The child records its OWN in-process snapshot in its result JSON; the
PARENT records DISK snapshots (sqlite rows / temp files / deck count) around
each child. The parent's own widget/thread/RSS numbers are meaningless for a
cross-process child (different process), so for cross-process the leak signal
lives in the disk deltas + the children's self-reported in-process snapshots.

Parent vs. child snapshot semantics (cross-process)
---------------------------------------------------
* PARENT: ``capture_snapshot(test_home, gateway=...)`` before/after each child —
  authoritative for DISK (sqlite/temp) and DECK metrics.
* CHILD: ``capture_snapshot`` inside its own process — authoritative for that
  child's in-process Qt/thread/RSS metrics, written into its result JSON and
  read back by the parent.
The :class:`SoakReport` for cross-process keeps the CHILDREN's ``snapshot_post``
in ``sessions`` (so ``detect_divergence`` sees per-child in-process numbers), and
records the parent disk deltas alongside in ``config`` context.

Temp media and ``temp_files``
-----------------------------
Temp media is cleaned after each session (``EpisodeProcessor`` default, mirroring
production). ``ANKI_MINER_KEEP_TEMP`` is NOT forced by the harness, so
``temp_files`` in the snapshot is a genuine leak metric: it is non-zero between
sessions only if a cleanup bug prevents the temp folder from being removed. To
retain temp media for debugging, export ``ANKI_MINER_KEEP_TEMP=1`` before running.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import faulthandler
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tests._home_isolation import guard_real_home, set_test_home
from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError
from tests.e2e.app_config import build_app_config
from tests.e2e.app_driver import AppDriver
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.curation import AutoCurationResponder
from tests.e2e.driver import CancelOutcome, E2EMiningError, E2ETimeout, EpisodeTabDriver
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video
from tests.e2e.fixtures_subtitle import EXPECTED_LEMMAS, get_test_srt
from tests.e2e.instrumentation import (
    StateSnapshot,
    capture_snapshot,
    detect_divergence,
    diff_snapshots,
)
from tests.e2e.screenshot_diff import SCREENSHOT_DIFF_WARN_THRESHOLD
from tests.e2e.screenshot_diff import screenshot_diff as _screenshot_diff

__all__ = [
    "SessionReport",
    "SoakReport",
    "_assert_safe_home",
    "_check_known_words_cross_session",
    "_child_cmd",
    "_prepare_home",
    "_read_known_word_count",
    "_read_known_words_set",
    "run_crossprocess_soak",
    "run_inprocess_soak",
    "run_one_session",
]

# Number of log lines kept on each SessionReport (a tail, not the whole log).
_LOG_TAIL_LINES = 40
# Watchdog: if a single wait runs this far past the result budget, dump every
# thread's traceback into the run dir — a hang may BE the bug reproducing.
_HANG_DUMP_MARGIN_S = 30.0


@dataclass
class SessionReport:
    """One mining session's outcome + the state captured around it.

    All fields are JSON-friendly so ``dataclasses.asdict`` + ``json.dump`` round-
    trips straight into ``report.json`` / the child handoff JSON.
    """

    index: int = 0
    ok: bool = False
    wall_s: float = 0.0
    words_found: int = 0
    cards_created: int = 0
    errors: list[str] = field(default_factory=list)
    #: One entry per curation dialog the responder answered, each the offered words.
    curation_offered: list[list] = field(default_factory=list)
    snapshot_pre: StateSnapshot | None = None
    snapshot_post: StateSnapshot | None = None
    #: ``diff_snapshots(pre, post)`` output (per-metric delta) or ``{}``.
    delta: dict = field(default_factory=dict)
    #: Screenshot filename written into the run dir for this session.
    screenshot: str = ""
    #: Last ~40 lines of the driver's activity log.
    log_tail: str = ""
    #: GUI-state checks recorded after the run completes.  Keys are check names;
    #: values are ``{"expected": ..., "actual": ..., "ok": bool}``.  Empty when
    #: the run did not reach the GUI-check step (e.g. timed out before result).
    gui_checks: dict = field(default_factory=dict)
    #: Mined forms observed in this session (from ``ProcessingResult.mined_forms``).
    #: Empty when the run did not reach the mined-set check (e.g. timed out).
    mined_forms: list[str] = field(default_factory=list)
    #: Cancel-path outcome when this session injected a cancel (else ``{}``).
    #: Keys: ``cancelled`` / ``joined`` / ``buttons_idle`` — see ``CancelOutcome``.
    cancel_outcome: dict = field(default_factory=dict)
    #: Total known-word rows in ``known_words.db`` after this session.
    #: ``-1`` when the DB was absent or unreadable (preview / bypass runs).
    known_words_count: int = -1
    #: ``True`` when none of THIS session's mined_forms were re-mined in the NEXT
    #: session (subtraction worked).  ``None`` when not yet checked (i.e. this is the
    #: LAST session or the run is not in faithful mode).  Only set in faithful mode;
    #: always ``None`` in preview / bypass mode.
    known_words_not_remined: bool | None = None
    #: Normalized per-pixel mean absolute difference vs. session-0 baseline
    #: screenshot (0.0 = identical, 1.0 = maximum difference).  ``None`` when
    #: PIL is unavailable, a screenshot is missing, or this IS the baseline
    #: (session 0).
    screenshot_diff: float | None = None


@dataclass
class SoakReport:
    """Aggregate of a whole soak run (in-process or cross-process)."""

    mode: Literal["inprocess", "crossprocess"] = "inprocess"
    sessions: list[SessionReport] = field(default_factory=list)
    divergence: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    verdict: str = "PASS"
    artifacts_dir: str = ""

    def write_report(self, run_dir: RunDir) -> Path:
        """Dump this report as ``report.json`` under ``run_dir`` and return its path."""
        path = run_dir.path / "report.json"
        path.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path


# --------------------------------------------------------------------------
# one session
# --------------------------------------------------------------------------


def _log_tail(text: str, lines: int = _LOG_TAIL_LINES) -> str:
    """Return the last ``lines`` lines of ``text``."""
    return "\n".join(text.splitlines()[-lines:])


# Log markers guaranteed to appear in both preview and process runs (emitted by
# the presenter in phase 1 + phase 2 of EpisodeProcessor.process_episode).
# "Step 1/5" is in the show_info call at the top of _phase1_parse; "Step 2/5"
# at the top of _phase2_filter.  Both appear regardless of preview vs process.
_LOG_MARKERS_COMMON = ("Step 1/5", "Step 2/5")

# Log marker for process mode only: phase 5 creation (preview skips phases 3-5).
_LOG_MARKER_PROCESS_ONLY = "Step 5/5"


def _check_gui_state(
    driver: EpisodeTabDriver | AppDriver,
    *,
    preview: bool,
    result_ok: bool,
) -> dict:
    """Inspect post-run GUI state and return a dict of named check results.

    Each entry maps a check name to ``{"expected": ..., "actual": ..., "ok": bool}``.
    All checks are RECORDED unconditionally so the report is self-documenting
    even when some pass and some fail.  However, only when ``result_ok=True``
    do failing checks escalate to errors — when the run itself failed, we record
    observed widget state as data without adding new GUI-specific error noise.

    Only called when the run COMPLETED (result captured, no exception) — the
    caller is responsible for guarding.  Checks are tolerant of preview vs.
    process: phase-3..5 log markers are only required in process mode.
    """
    checks: dict = {}

    # 1. Buttons returned to idle after the worker finished.
    # Pump the event loop briefly so the queued _restore_buttons signal
    # (connected to worker.finished) is delivered before we sample button state.
    # This prevents a false-FAIL on slow machines where the queued signal has
    # not yet been dispatched when we reach this check.
    from tests.e2e.driver import _drain_until

    _drain_until(lambda: driver.buttons_idle(), timeout_ms=2000)
    buttons_ok = driver.buttons_idle()
    checks["buttons_idle"] = {
        "expected": True,
        "actual": buttons_ok,
        # Only a CONSISTENCY check when result_ok; otherwise record-only.
        "ok": buttons_ok if result_ok else True,
        "desc": "process_button enabled+visible AND cancel_button hidden",
    }

    # 2. Log is non-empty.
    log = driver.log_text()
    log_nonempty = bool(log.strip())
    checks["log_nonempty"] = {
        "expected": True,
        "actual": log_nonempty,
        "ok": log_nonempty if result_ok else True,
        "desc": "activity log contains at least one line",
    }

    # 3. Common phase markers appear IN ORDER in the log (Step 1/5 then Step 2/5).
    # Verify each marker is present AND their indices are strictly increasing.
    marker_indices: list[int] = []
    for marker in _LOG_MARKERS_COMMON:
        key = f"log_contains:{marker}"
        found = marker in log
        checks[key] = {
            "expected": True,
            "actual": found,
            "ok": (found if result_ok else True),
            "desc": f"log contains '{marker}'",
        }
        if found:
            marker_indices.append(log.index(marker))

    # Assert strict ordering only when all common markers were found.
    if len(marker_indices) == len(_LOG_MARKERS_COMMON):
        in_order = all(marker_indices[i] < marker_indices[i + 1] for i in range(len(marker_indices) - 1))
        checks["log_markers_in_order"] = {
            "expected": True,
            "actual": in_order,
            "ok": (in_order if result_ok else True),
            "desc": "phase markers appear in the log in strictly increasing order",
        }

    # 4. Process-only marker: Step 5/5 — only asserted when not preview.
    if not preview:
        marker = _LOG_MARKER_PROCESS_ONLY
        key = f"log_contains:{marker}"
        found = marker in log
        checks[key] = {
            "expected": True,
            "actual": found,
            "ok": (found if result_ok else True),
            "desc": f"log contains '{marker}' (process mode only)",
        }

    # 5. Progress state — always recorded as data; never skip.
    prog_value = driver.progress_value()
    prog_text = driver.progress_text()
    checks["progress_value"] = {
        "expected": "recorded",
        "actual": prog_value,
        # Not a pass/fail assertion — pure data.
        "ok": True,
        "desc": "progress bar value after run (data only)",
    }
    checks["progress_text"] = {
        "expected": "recorded",
        "actual": prog_text,
        "ok": True,
        "desc": "progress status label text after run (data only)",
    }

    # 6. Process-mode stuck-UI check: fail only on clearly broken "stuck" state.
    # A healthy run advances progress > 0 OR sets a non-idle status string.
    # We deliberately do NOT assert an exact value (==100) or exact string
    # ("Complete"), since precise end-state varies and strict checks would
    # false-FAIL on the real run.  Preview leaves progress at 0 by design
    # (callback not invoked) — never checked for preview.
    if not preview and result_ok:
        _idle_statuses = {"", "Ready"}
        status_idle = prog_text.strip() in _idle_statuses
        stuck = prog_value == 0 and status_idle
        checks["progress_not_stuck"] = {
            "expected": False,
            "actual": stuck,
            "ok": not stuck,
            "desc": (
                "progress advanced (value>0) OR status is non-idle after process run "
                "— stuck (value=0 AND idle status) signals the 'stuck progress bar' bug class"
            ),
        }

    return checks


def _run_cancel_session(
    driver: EpisodeTabDriver | AppDriver,
    *,
    report: SessionReport,
    preview: bool,
    delay_s: float,
    timeout_s: float,
    index: int,
) -> None:
    """Drive a cancel-injected run and record the outcome on ``report``.

    Starts the run (preview or process), schedules a Cancel ``delay_s`` seconds
    in, and waits for the worker to FINISH (a cancelled run emits no result/error,
    only ``finished``). Asserts the run-end invariants: the worker joined (no
    leaked thread) AND the tab returned to idle (reusable for the next session).
    Either failing sets ``report.ok = False`` with a message. The cancel is
    allowed to lose the race against a very fast run — that is recorded, not
    failed (see ``EpisodeTabDriver.cancel_and_wait``).
    """
    if preview:
        driver.click_preview()
    else:
        driver.click_process()

    outcome: CancelOutcome = driver.cancel_and_wait(delay_s=delay_s, timeout_s=timeout_s)
    report.cancel_outcome = {
        "cancelled": outcome.cancelled,
        "joined": outcome.joined,
        "buttons_idle": outcome.buttons_idle,
    }
    report.screenshot = driver.screenshot(f"session-{index}-cancel").name

    # Run-end invariants: a cancel that leaks the worker thread or leaves the tab
    # stuck (cancel button still showing / process disabled) is the bug class.
    report.ok = True
    if not outcome.joined:
        report.ok = False
        report.errors.append("cancel: worker thread did not join (leaked/stuck thread)")
    if not outcome.buttons_idle:
        report.ok = False
        report.errors.append("cancel: tab did not return to idle after cancel (stuck UI)")


def run_one_session(
    e2e: E2EConfig,
    *,
    test_home: Path,
    preview: bool,
    bypass_known_words: bool,
    run_dir: RunDir,
    index: int,
    driver: EpisodeTabDriver | AppDriver | None = None,
    gateway: AnkiGateway | None = None,
    inject_cancel: float | None = None,
) -> SessionReport:
    """Mine one episode and return a :class:`SessionReport`.

    Args:
        e2e: Harness config (curation policy, timeouts, deck name).
        test_home: Isolated home for all on-disk state.
        preview: ``True`` clicks Preview (parse+filter only, no Anki);
            ``False`` clicks Process (creates cards, needs Anki unless
            ``bypass_known_words``).
        bypass_known_words: Passed to ``build_app_config`` (see its docstring).
        run_dir: Artifact dir for screenshots/JSON.
        index: Session ordinal (0-based) for snapshots/reporting.
        driver: Reuse this driver (in-process loop) when given; otherwise build a
            fresh one (cross-process child path).
        gateway: Optional Anki gateway for the deck-count snapshot metric.
        inject_cancel: When set (seconds), this session clicks Cancel that many
            seconds after starting the run instead of waiting for a result. It
            asserts the run ENDS promptly (worker joins, no leaked thread) and the
            tab returns to idle so the NEXT session can reuse it — a cancelled run
            emits no result/error, only ``finished``. See ``_run_cancel_session``.

    A timeout / mining error is RECORDED on the report (``ok=False`` + message +
    screenshot), never re-raised, so one bad session does not abort the soak.
    """
    test_home = Path(test_home)
    # Seed the offline dict if absent (idempotent across sessions / processes).
    dicts_root = test_home / "dicts"
    if not (dicts_root / "e2e-dict" / "index.sqlite").is_file():
        seed_offline_dict(dicts_root)

    cfg = build_app_config(e2e, test_home, bypass_known_words=bypass_known_words)
    media_temp = cfg.media_temp_folder

    own_driver = driver is None
    if own_driver:
        driver = EpisodeTabDriver(cfg, run_dir)
    assert driver is not None

    responder = AutoCurationResponder(policy=e2e.curation_policy, first_n=e2e.first_n)
    report = SessionReport(index=index)

    snap_pre = capture_snapshot(
        test_home=test_home,
        media_temp_folder=media_temp,
        gateway=gateway,
        index=index,
        label=f"pre-session-{index}",
    )
    report.snapshot_pre = snap_pre

    start = time.monotonic()
    hang_dump = run_dir.path / f"hang_session_{index}.txt"
    try:
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())

        if inject_cancel is not None:
            # Cancel path: click Cancel mid-run instead of waiting for a result.
            # A cancelled run emits no result/error (only finished), so this
            # asserts the run ends promptly, the worker joins, and the tab
            # returns to idle. Skips the normal result/mined-set assertions.
            _run_cancel_session(
                driver,
                report=report,
                preview=preview,
                delay_s=inject_cancel,
                timeout_s=e2e.result_timeout_s,
                index=index,
            )
        else:
            with responder:
                if preview:
                    driver.click_preview()
                else:
                    driver.click_process()
                # A hung wait may BE the bug; arm a watchdog that dumps all thread
                # stacks into the run dir if the wait blows well past its budget.
                dump_fh = open(hang_dump, "w", encoding="utf-8")  # noqa: SIM115
                try:
                    faulthandler.dump_traceback_later(
                        e2e.result_timeout_s + _HANG_DUMP_MARGIN_S,
                        file=dump_fh,
                    )
                    result = driver.wait_for_result(e2e.result_timeout_s)
                finally:
                    faulthandler.cancel_dump_traceback_later()
                    dump_fh.close()
                    # No hang fired → drop the empty dump file.
                    if hang_dump.is_file() and hang_dump.stat().st_size == 0:
                        with contextlib.suppress(OSError):
                            hang_dump.unlink()
            report.ok = result.success
            report.words_found = result.total_words_found
            report.cards_created = result.cards_created
            report.errors = list(result.errors)
            report.screenshot = driver.screenshot(f"session-{index}").name

            # GUI-state consistency checks: assert widget state is coherent after
            # the run.  Recorded in all cases; a failing check surfaces in the
            # verdict via _assemble_report's any-failed guard.
            gui_checks = _check_gui_state(driver, preview=preview, result_ok=result.success)
            report.gui_checks = gui_checks
            failed_checks = [name for name, c in gui_checks.items() if not c["ok"]]
            if failed_checks:
                report.ok = False
                for name in failed_checks:
                    c = gui_checks[name]
                    report.errors.append(
                        f"GUI check failed [{name}]: expected={c['expected']!r} actual={c['actual']!r} — {c['desc']}"
                    )

            # Mined word-set assertion: record observed forms and flag divergence.
            # Only assert when the run completed ok (don't add noise to an
            # already-failed run).
            report.mined_forms = sorted(result.mined_forms)
            if result.success:
                observed = set(result.mined_forms)
                expected = set(EXPECTED_LEMMAS)
                if bypass_known_words:
                    # All words mined deterministically — set must match exactly.
                    if observed != expected:
                        extra = observed - expected
                        missing = expected - observed
                        report.ok = False
                        report.errors.append(
                            f"mined-set mismatch (bypass): "
                            f"observed={sorted(observed)!r}, "
                            f"extra={sorted(extra)!r}, "
                            f"missing={sorted(missing)!r}"
                        )
                else:
                    # Faithful mode: known-words subtraction yields a subset.
                    if not observed <= expected:
                        spurious = observed - expected
                        report.ok = False
                        report.errors.append(
                            f"mined-set not a subset (faithful): "
                            f"spurious={sorted(spurious)!r} not in EXPECTED_LEMMAS"
                        )
    except (E2ETimeout, E2EMiningError) as exc:
        report.ok = False
        report.errors = [f"{type(exc).__name__}: {exc}"]
        with contextlib.suppress(Exception):
            report.screenshot = driver.screenshot(f"session-{index}-failed").name
    except Exception as exc:  # defensive: any other failure is recorded, not fatal
        report.ok = False
        report.errors = [f"{type(exc).__name__}: {exc}"]
        with contextlib.suppress(Exception):
            report.screenshot = driver.screenshot(f"session-{index}-failed").name
    finally:
        report.wall_s = time.monotonic() - start
        report.curation_offered = [list(words) for words in responder.offered]
        with contextlib.suppress(Exception):
            report.log_tail = _log_tail(driver.log_text())

    snap_post = capture_snapshot(
        test_home=test_home,
        media_temp_folder=media_temp,
        gateway=gateway,
        index=index,
        label=f"post-session-{index}",
    )
    report.snapshot_post = snap_post
    report.delta = diff_snapshots(snap_pre, snap_post)

    if own_driver:
        # An own-driver is never reused, so fully dispose it (releases the tab
        # QWidget, not just the worker — see EpisodeTabDriver.dispose).
        with contextlib.suppress(Exception):
            driver.dispose()

    return report


# --------------------------------------------------------------------------
# divergence + report assembly (shared by both loop shapes)
# --------------------------------------------------------------------------


def _drain_qt_deletes() -> None:
    """Flush pending Qt deferred-deletes (the conftest idiom) between sessions.

    Destroys C++ objects scheduled via ``deleteLater`` so a leaked widget shows
    up as a genuine leak in the next session's snapshot rather than lingering.
    """
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _read_known_word_count(test_home: Path) -> int:
    """Return the total row count from ``known_words.db``, or ``-1`` if absent/unreadable.

    Pure read: opens the DB in read-only URI mode so it never creates a new file
    and cannot interfere with the running pipeline.  Degrades gracefully when the
    table has not been initialised yet (preview / bypass runs never create the DB).
    """
    import sqlite3

    db_path = Path(test_home) / "known_words.db"
    if not db_path.exists():
        return -1
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM known_words")
            return int(cursor.fetchone()[0])
    except Exception:
        return -1


def _read_known_words_set(test_home: Path) -> set[str] | None:
    """Return all ``lemma`` values from ``known_words.db``, or ``None`` if absent/unreadable.

    Same read-only URI approach as :func:`_read_known_word_count`.
    Returns ``None`` (not empty set) when the DB is absent so callers can
    distinguish "DB not there" from "DB exists but empty".
    """
    import sqlite3

    db_path = Path(test_home) / "known_words.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            cursor = conn.execute("SELECT lemma FROM known_words")
            return {row[0] for row in cursor.fetchall()}
    except Exception:
        return None


def _check_known_words_cross_session(
    prev: SessionReport,
    curr: SessionReport,
    *,
    test_home: Path,
) -> None:
    """Check cross-session known-words invariants (faithful mode only).

    After session N completes, reads ``known_words.db`` and verifies:
    1. The forms mined in session N-1 (``prev.mined_forms``) are now in the
       known-words set (they were written as known after being mined).
    2. Session N did NOT re-mine any of session N-1's forms (subtraction worked).

    A violated invariant sets ``prev.ok = False`` and appends a descriptive error.
    ``curr.known_words_not_remined`` is set on ``prev`` (it reflects whether PREV's
    mined forms were NOT re-mined by CURR).

    Only called in faithful mode; degrade gracefully if the DB is absent (no crash,
    skip both checks and leave ``prev.known_words_not_remined = None``).
    """
    known = _read_known_words_set(test_home)
    if known is None:
        # DB absent — likely a preview or the pipeline didn't create it.
        return

    prev_mined = set(prev.mined_forms)
    curr_mined = set(curr.mined_forms)

    # Check 1: prev's mined forms appear as known by the time curr ran.
    if prev_mined:
        not_recorded = prev_mined - known
        if not_recorded:
            prev.ok = False
            prev.errors.append(
                f"known-words accumulation: {len(not_recorded)} form(s) mined in session "
                f"{prev.index} not found in known_words.db by session {curr.index}: "
                f"{sorted(not_recorded)!r}"
            )

    # Check 2: curr must not re-mine any of prev's forms.
    if prev_mined:
        remined = prev_mined & curr_mined
        not_remined = len(remined) == 0
        prev.known_words_not_remined = not_remined
        if not not_remined:
            prev.ok = False
            prev.errors.append(
                f"known-words subtraction failed: {len(remined)} form(s) mined in session "
                f"{prev.index} were re-mined in session {curr.index}: "
                f"{sorted(remined)!r}"
            )
    else:
        # Nothing was mined in prev — vacuously ok.
        prev.known_words_not_remined = True


def _assemble_report(
    *,
    mode: Literal["inprocess", "crossprocess"],
    sessions: list[SessionReport],
    e2e: E2EConfig,
    test_home: Path,
    preview: bool,
    bypass_known_words: bool,
    run_dir: RunDir,
    extra_config: dict | None = None,
) -> SoakReport:
    """Build the :class:`SoakReport`: run divergence + derive the verdict.

    Divergence is run over each session's POST snapshot (the natural choice — the
    state left behind by each session) with the per-session ``cards_created``
    series passed through. The soak verdict mirrors the divergence verdict, but is
    forced to ``FAIL`` if ANY session did not complete ok (a crashed/timed-out
    session is itself a failure regardless of the trend).
    """
    post_snaps = [s.snapshot_post for s in sessions if s.snapshot_post is not None]
    cards = [s.cards_created for s in sessions]
    divergence = detect_divergence(post_snaps, cards_created=cards, mode=mode)

    any_failed = any(not s.ok for s in sessions)
    verdict = "FAIL" if any_failed else divergence.verdict

    # Visual-regression WARN: if any non-baseline session has a diff above the
    # threshold, escalate to WARN (never FAIL — rendering noise exists).
    visual_warn_sessions = [
        s.index
        for s in sessions
        if s.screenshot_diff is not None and s.screenshot_diff > SCREENSHOT_DIFF_WARN_THRESHOLD
    ]
    if visual_warn_sessions and verdict == "PASS":
        verdict = "WARN"

    config = {
        "deck_name": e2e.deck_name,
        "test_home": str(test_home),
        "ankiconnect_url": e2e.ankiconnect_url,
        "curation_policy": e2e.curation_policy,
        "first_n": e2e.first_n,
        "preview": preview,
        "bypass_known_words": bypass_known_words,
        "sessions_requested": len(sessions),
        "screenshot_diff_warn_threshold": SCREENSHOT_DIFF_WARN_THRESHOLD,
        "screenshot_diff_warn_sessions": visual_warn_sessions,
    }
    if extra_config:
        config.update(extra_config)

    return SoakReport(
        mode=mode,
        sessions=sessions,
        divergence=dataclasses.asdict(divergence),
        config=config,
        verdict=verdict,
        artifacts_dir=str(run_dir.path),
    )


def _assert_safe_home(test_home: Path) -> None:
    """Refuse to run against the real ``~/.anki_miner`` (a hard safety gate)."""
    real_home = (Path.home() / ".anki_miner").resolve()
    if Path(test_home).resolve() == real_home:
        raise AssertionError(
            f"Refusing to run the soak against the real anki_miner home {real_home}. "
            f"Configure an isolated test_home."
        )


def _prepare_home(test_home: Path, *, fresh: bool) -> dict:
    """Assert safety, record pre-run baseline, and optionally wipe the home.

    Returns a dict of baseline keys to include in ``SoakReport.config``:
    * ``home_pre_existed`` — ``True`` if the directory already existed before
      this call (baseline populated from the on-disk state before any wipe).
    * ``home_baseline`` — ``{known_words_rows, temp_files}`` sampled from the
      pre-wipe state so a skewed faithful run is self-explaining in
      ``report.json``.

    When ``fresh=True`` the home's contents are deleted after sampling (the dir
    itself is recreated so the run can write into it).  The real ``~/.anki_miner``
    is always refused via ``_assert_safe_home``.
    """
    from tests.e2e.app_config import MEDIA_TEMP_BASENAME
    from tests.e2e.instrumentation import _count_sqlite_rows, _count_temp_files

    test_home = Path(test_home)
    _assert_safe_home(test_home)

    pre_existed = test_home.exists()
    known_rows = _count_sqlite_rows(test_home / "known_words.db") if pre_existed else 0
    temp_files = _count_temp_files(test_home / MEDIA_TEMP_BASENAME) if pre_existed else 0

    if fresh and pre_existed:
        import shutil

        shutil.rmtree(test_home)

    test_home.mkdir(parents=True, exist_ok=True)

    return {
        "home_pre_existed": pre_existed,
        "home_baseline": {
            "known_words_rows": known_rows,
            "temp_files": temp_files,
        },
    }


def _maybe_gateway(e2e: E2EConfig, *, preview: bool) -> AnkiGateway | None:
    """Return a ready gateway (deck ensured) for a live process run, else ``None``.

    For preview / bypass runs Anki is not used, so no gateway is built. For a live
    process run, the deck is ensured up front; an unreachable Anki yields ``None``
    (the caller — a test — gates on this and skips).

    Raises:
        ForeignDeckError: The test deck already exists with notes from a prior run.
            Let this propagate so the runner can surface a clean, actionable message.
    """
    if preview:
        return None
    try:
        gateway = AnkiGateway(e2e)
        gateway.ping()
    except AnkiUnreachableError:
        return None
    gateway.ensure_test_deck()  # raises ForeignDeckError if deck has prior-run notes
    gateway.ensure_test_model()
    return gateway


# --------------------------------------------------------------------------
# in-process soak
# --------------------------------------------------------------------------


def run_inprocess_soak(
    e2e: E2EConfig,
    *,
    sessions: int,
    preview: bool,
    bypass_known_words: bool,
    run_dir: RunDir,
    test_home: Path,
    fresh_home: bool = False,
    inject_cancel: float | None = None,
    full_window: bool = False,
) -> SoakReport:
    """Mine ``sessions`` times reusing ONE driver/tab; return a :class:`SoakReport`.

    Reusing the single tab across sessions catches widget/worker/QThread/memory
    leaks AND GUI-consistency bugs (button state, word-set correctness, cancel
    path behaviour) that only surface with real services wired to the real widget
    stack. Between sessions the driver is torn down and Qt deferred-deletes are
    drained so a surviving object is a genuine leak in the next snapshot.

    SAFETY: refuses the real home and wraps the whole run in
    :func:`guard_real_home` so the user's ``~/.anki_miner`` is provably untouched.

    Args:
        fresh_home: When ``True``, delete the test home's contents before
            running so the run starts clean (idempotent, safe — ``_prepare_home``
            always refuses the real home). The pre-wipe baseline is recorded in
            ``SoakReport.config`` regardless of this flag.
        inject_cancel: When set (seconds), ONE extra dedicated cancel session is
            appended after the normal sessions: it starts a run, clicks Cancel
            after the delay, and asserts the run ends promptly with the tab
            reusable. The cancel is its OWN session, never folded into every soak
            iteration (which would corrupt the leak series).
        full_window: When ``True``, drive the run through a real
            :class:`~tests.e2e.app_driver.AppDriver` (a full ``MainWindow`` with
            the episode tab mounted + dialogs patched) instead of the bare
            ``EpisodeTabDriver`` — so dialog wiring / tab switching / the results
            display are exercised too. In-process only.
    """
    test_home = Path(test_home)
    home_info = _prepare_home(test_home, fresh=fresh_home)

    cfg = build_app_config(e2e, test_home, bypass_known_words=bypass_known_words)
    if not (cfg.dicts_root / "e2e-dict" / "index.sqlite").is_file():
        seed_offline_dict(cfg.dicts_root)

    reports: list[SessionReport] = []
    gateway: AnkiGateway | None = None
    any_failed = False

    with guard_real_home(Path.home() / ".anki_miner"):
        gateway = _maybe_gateway(e2e, preview=preview)
        # ONE driver reused across every session (leak detection depends on it).
        # Full-window builds a real MainWindow (with the episode tab mounted +
        # ResultsDialog/WordPreviewDialog/WelcomeDialog/curation patched); the
        # default path drives the bare tab. AppDriver holds the dialog/responder
        # patches open for its lifetime, so the responder used per session by
        # run_one_session simply re-patches curation (a harmless re-entry).
        driver: EpisodeTabDriver | AppDriver
        if full_window:
            driver = AppDriver(cfg, run_dir, curation_policy=e2e.curation_policy, first_n=e2e.first_n)
        else:
            driver = EpisodeTabDriver(cfg, run_dir)
        # Whether cross-session known-words checks are active: faithful mode only.
        # Preview / bypass legitimately re-mines and never writes known_words.db,
        # so these asserts would be meaningless noise there.
        faithful = not preview and not bypass_known_words

        baseline_screenshot: Path | None = None
        try:
            for i in range(sessions):
                report = run_one_session(
                    e2e,
                    test_home=test_home,
                    preview=preview,
                    bypass_known_words=bypass_known_words,
                    run_dir=run_dir,
                    index=i,
                    driver=driver,
                    gateway=gateway,
                )
                # Record how many known words the DB holds after this session.
                # -1 in preview/bypass (DB not created); positive count in faithful.
                report.known_words_count = _read_known_word_count(test_home)

                # Screenshot baseline-diff: session 0 = baseline; subsequent
                # sessions are diffed against it. A diff above threshold is
                # surfaced as a WARN flag (visual regression, not a hard FAIL).
                if report.screenshot:
                    shot_path = run_dir.path / report.screenshot
                    if baseline_screenshot is None:
                        baseline_screenshot = shot_path  # session 0 sets the baseline
                    else:
                        diff_val = _screenshot_diff(baseline_screenshot, shot_path)
                        report.screenshot_diff = diff_val

                reports.append(report)

                # Cross-session check: compare THIS session to the PREVIOUS one.
                # The check runs after session 1+ (needs a prior session to compare).
                if faithful and len(reports) >= 2:
                    _check_known_words_cross_session(
                        reports[-2],
                        reports[-1],
                        test_home=test_home,
                    )

                any_failed = any_failed or not report.ok
                # Between-session teardown + Qt deferred-delete drain so leaks
                # surface in the next session's snapshot.
                with contextlib.suppress(Exception):
                    driver.teardown()
                _drain_qt_deletes()

            # Dedicated cancel session (opt-in): runs AFTER the normal soak so it
            # never perturbs the leak series. Reuses the SAME tab to prove the tab
            # stays reusable after a cancel.
            if inject_cancel is not None:
                cancel_report = run_one_session(
                    e2e,
                    test_home=test_home,
                    preview=preview,
                    bypass_known_words=bypass_known_words,
                    run_dir=run_dir,
                    index=sessions,
                    driver=driver,
                    gateway=gateway,
                    inject_cancel=inject_cancel,
                )
                reports.append(cancel_report)
                any_failed = any_failed or not cancel_report.ok
                with contextlib.suppress(Exception):
                    driver.teardown()
                _drain_qt_deletes()
        finally:
            # Final disposal of the reused tab: dispose() releases the tab
            # QWidget (deleteLater) which teardown() deliberately does not, then
            # drain Qt deferred-deletes so its C++ object is destroyed within
            # this call rather than leaking to end-of-session.
            with contextlib.suppress(Exception):
                driver.dispose()
            _drain_qt_deletes()
            # Keep the deck on failure (for inspection); clean it otherwise.
            if gateway is not None and not any_failed:
                with contextlib.suppress(Exception):
                    gateway.delete_test_deck()

    soak = _assemble_report(
        mode="inprocess",
        sessions=reports,
        e2e=e2e,
        test_home=test_home,
        preview=preview,
        bypass_known_words=bypass_known_words,
        run_dir=run_dir,
        extra_config=home_info,
    )
    soak.write_report(run_dir)
    return soak


# --------------------------------------------------------------------------
# cross-process soak
# --------------------------------------------------------------------------


def _worktree_root() -> Path:
    """Repository / worktree root (``tests/`` parent), used as the child cwd.

    ``soak.py`` lives at ``<root>/tests/e2e/soak.py`` so the root is three parents
    up. The child runs ``-m tests.e2e.soak`` from here so the package import works.
    """
    return Path(__file__).resolve().parents[2]


def _child_cmd(
    e2e: E2EConfig,
    *,
    test_home: Path,
    index: int,
    out: Path,
    preview: bool,
    bypass_known_words: bool,
    run_dir: Path | None = None,
) -> list[str]:
    """Build the argv list for a ``--one-session`` child process.

    Pure (no I/O, no subprocess) so it can be unit-tested in isolation.
    All four parent config overrides (policy, first_n, deck, ankiconnect_url)
    are forwarded so the child does not silently fall back to defaults.

    Args:
        run_dir: When given, forwarded as ``--run-dir`` so the child writes
            its artifacts into the PARENT's run dir instead of creating its own
            timestamped subdir. Co-locates child screenshots with the parent
            report so ``SessionReport.screenshot`` resolves under the parent dir.
    """
    cmd = [
        sys.executable,
        "-m",
        "tests.e2e.soak",
        "--one-session",
        "--home",
        str(test_home),
        "--index",
        str(index),
        "--out",
        str(out),
        "--policy",
        e2e.curation_policy,
        "--first-n",
        str(e2e.first_n),
        "--deck",
        e2e.deck_name,
        "--ankiconnect-url",
        e2e.ankiconnect_url,
    ]
    if run_dir is not None:
        cmd.extend(["--run-dir", str(run_dir)])
    if preview:
        cmd.append("--preview")
    if bypass_known_words:
        cmd.append("--bypass-known-words")
    return cmd


def _run_child_session(
    e2e: E2EConfig,
    *,
    test_home: Path,
    index: int,
    preview: bool,
    bypass_known_words: bool,
    child_json: Path,
    run_dir: RunDir,
) -> SessionReport:
    """Spawn one fresh ``--one-session`` subprocess and read back its SessionReport.

    Temp media is NOT kept by default — the child cleans up after itself, mirroring
    production. This makes ``temp_files`` a genuine inter-session leak signal. If the
    operator has exported ``ANKI_MINER_KEEP_TEMP=1``, it passes through from the
    parent env for forensic media retention.

    A timeout / non-zero exit / unreadable JSON is recorded as ``ok=False`` with a
    stderr tail rather than raising, so a flaky child does not abort the soak.

    ``run_dir`` is the PARENT's run dir: forwarded to the child via ``--run-dir`` so
    the child writes its screenshot there instead of creating its own timestamped dir.
    """
    cmd = _child_cmd(
        e2e,
        test_home=test_home,
        index=index,
        out=child_json,
        preview=preview,
        bypass_known_words=bypass_known_words,
        run_dir=run_dir.path,
    )

    # --fresh-home is intentionally NOT forwarded to children: the parent wipes
    # the home ONCE before spawning children; each child then runs into a clean dir.
    # ANKI_MINER_KEEP_TEMP is intentionally NOT forced here: temp is cleaned after
    # each child session (mirroring production), so temp_files is a genuine leak
    # signal. If the parent env already exports it (operator debugging), it passes
    # through via **os.environ.
    env = {
        **os.environ,
        "ANKI_MINER_HOME": str(test_home),
        "QT_QPA_PLATFORM": "offscreen",
    }

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_worktree_root()),
            env=env,
            capture_output=True,
            text=True,
            timeout=e2e.session_timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        stderr_tail = _log_tail((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        return SessionReport(
            index=index,
            ok=False,
            errors=[f"child session {index} timed out after {e2e.session_timeout_s}s", stderr_tail],
        )

    if child_json.is_file():
        try:
            data = json.loads(child_json.read_text(encoding="utf-8"))
            return _session_report_from_dict(data)
        except Exception as exc:  # malformed JSON despite a file — record it
            return SessionReport(
                index=index,
                ok=False,
                errors=[f"child session {index} wrote unreadable JSON: {exc}", _log_tail(proc.stderr)],
            )

    return SessionReport(
        index=index,
        ok=False,
        errors=[
            f"child session {index} exited {proc.returncode} without writing {child_json.name}",
            _log_tail(proc.stderr),
        ],
    )


def _session_report_from_dict(data: dict) -> SessionReport:
    """Rebuild a :class:`SessionReport` from a child's JSON dict.

    Nested snapshots are rehydrated into :class:`StateSnapshot` so the parent's
    ``detect_divergence`` sees real snapshot objects (it reads attributes).
    """

    def _snap(d: dict | None) -> StateSnapshot | None:
        if not d:
            return None
        fields = {f.name for f in dataclasses.fields(StateSnapshot)}
        return StateSnapshot(**{k: v for k, v in d.items() if k in fields})

    fields = {f.name for f in dataclasses.fields(SessionReport)}
    kwargs = {k: v for k, v in data.items() if k in fields}
    kwargs["snapshot_pre"] = _snap(data.get("snapshot_pre"))
    kwargs["snapshot_post"] = _snap(data.get("snapshot_post"))
    return SessionReport(**kwargs)


def run_crossprocess_soak(
    e2e: E2EConfig,
    *,
    sessions: int,
    preview: bool,
    bypass_known_words: bool,
    run_dir: RunDir,
    test_home: Path,
    fresh_home: bool = False,
) -> SoakReport:
    """Spawn a fresh subprocess per session; aggregate children + parent disk deltas.

    Each child is a clean process (inherits ``ANKI_MINER_HOME`` pre-import). The
    parent captures DISK/DECK snapshots around each child; the SessionReports in
    the aggregate carry the CHILDREN's own in-process snapshots (read back from
    their JSON). Parent disk deltas per session are recorded in ``config`` context.

    SAFETY: refuses the real home; wraps the run in :func:`guard_real_home`.

    Args:
        fresh_home: When ``True``, delete the test home's contents before
            running so the run starts clean. See ``_prepare_home`` for safety
            details and baseline recording.
    """
    test_home = Path(test_home)
    home_info = _prepare_home(test_home, fresh=fresh_home)

    cfg = build_app_config(e2e, test_home, bypass_known_words=bypass_known_words)
    media_temp = cfg.media_temp_folder
    if not (cfg.dicts_root / "e2e-dict" / "index.sqlite").is_file():
        seed_offline_dict(cfg.dicts_root)

    reports: list[SessionReport] = []
    parent_disk_deltas: list[dict] = []
    any_failed = False

    # Cross-session known-words checks: faithful mode only (not preview/bypass).
    faithful = not preview and not bypass_known_words

    with guard_real_home(Path.home() / ".anki_miner"):
        gateway = _maybe_gateway(e2e, preview=preview)
        baseline_screenshot: Path | None = None
        try:
            for i in range(sessions):
                parent_pre = capture_snapshot(
                    test_home=test_home,
                    media_temp_folder=media_temp,
                    gateway=gateway,
                    index=i,
                    label=f"parent-pre-{i}",
                )
                child_json = run_dir.path / f"child_session_{i}.json"
                report = _run_child_session(
                    e2e,
                    test_home=test_home,
                    index=i,
                    preview=preview,
                    bypass_known_words=bypass_known_words,
                    child_json=child_json,
                    run_dir=run_dir,
                )
                # Record known-word count from the shared on-disk DB (parent reads it).
                report.known_words_count = _read_known_word_count(test_home)

                # Screenshot baseline-diff: session 0 = baseline; subsequent
                # sessions are diffed against it. A diff above threshold is
                # surfaced as a WARN flag (visual regression, not a hard FAIL).
                if report.screenshot:
                    shot_path = run_dir.path / report.screenshot
                    if baseline_screenshot is None:
                        baseline_screenshot = shot_path  # session 0 sets the baseline
                    else:
                        diff_val = _screenshot_diff(baseline_screenshot, shot_path)
                        report.screenshot_diff = diff_val

                reports.append(report)

                # Cross-session check: compare to the previous session.
                if faithful and len(reports) >= 2:
                    _check_known_words_cross_session(
                        reports[-2],
                        reports[-1],
                        test_home=test_home,
                    )

                any_failed = any_failed or not report.ok
                parent_post = capture_snapshot(
                    test_home=test_home,
                    media_temp_folder=media_temp,
                    gateway=gateway,
                    index=i,
                    label=f"parent-post-{i}",
                )
                parent_disk_deltas.append({"index": i, "delta": diff_snapshots(parent_pre, parent_post)})
        finally:
            if gateway is not None and not any_failed:
                with contextlib.suppress(Exception):
                    gateway.delete_test_deck()

    soak = _assemble_report(
        mode="crossprocess",
        sessions=reports,
        e2e=e2e,
        test_home=test_home,
        preview=preview,
        bypass_known_words=bypass_known_words,
        run_dir=run_dir,
        extra_config={
            **home_info,
            "parent_disk_deltas": parent_disk_deltas,
            "note": (
                "cross-process: parent disk deltas are authoritative for sqlite/temp/"
                "deck; per-session in-process Qt/thread/RSS numbers are each child's own."
            ),
        },
    )
    soak.write_report(run_dir)
    return soak


# --------------------------------------------------------------------------
# child entry point: `python -m tests.e2e.soak --one-session ...`
# --------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    """``--one-session`` child entry: mine once, write the SessionReport JSON.

    The child inherits ``ANKI_MINER_HOME`` from its env (set by the parent BEFORE
    Python imports anki_miner), so its on-disk state already points at the test
    home. ``set_test_home`` is belt-and-suspenders to also patch any in-process
    home snapshot. Exits 0 on a completed session (``ok=True``), 1 otherwise.
    """
    parser = argparse.ArgumentParser(prog="tests.e2e.soak")
    parser.add_argument("--one-session", action="store_true", required=True)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--bypass-known-words", action="store_true")
    # Config forwarded from the parent so cross-process children honour all overrides.
    parser.add_argument("--policy", default="all", choices=["all", "first_n", "none"])
    parser.add_argument("--first-n", type=int, default=0)
    parser.add_argument("--deck", default=None)
    parser.add_argument("--ankiconnect-url", default=None)
    # When given, the child writes its artifacts into the PARENT's run dir (no
    # new timestamped subdir is created), co-locating screenshots with the parent
    # report so SessionReport.screenshot resolves under the parent run_dir.path.
    parser.add_argument("--run-dir", default=None, type=Path)
    args = parser.parse_args(argv)

    test_home = Path(args.home)
    test_home.mkdir(parents=True, exist_ok=True)
    _assert_safe_home(test_home)
    # Re-assert env + patch any in-process home snapshot (the child set the env
    # pre-import, but this also covers transitive imports).
    set_test_home(test_home)

    # Headless Qt for the child (the parent also sets QT_QPA_PLATFORM in env).
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    # Build E2EConfig forwarding all parent overrides; fall back to defaults for
    # optional flags so existing callers that omit them stay valid.
    e2e_kwargs: dict = {"test_home": test_home, "curation_policy": args.policy, "first_n": args.first_n}
    if args.deck is not None:
        e2e_kwargs["deck_name"] = args.deck
    if args.ankiconnect_url is not None:
        e2e_kwargs["ankiconnect_url"] = args.ankiconnect_url
    e2e = E2EConfig(**e2e_kwargs)
    # Use the parent's run dir when given (avoids creating a separate child dir);
    # fall back to a labelled child subdir for standalone/debugging invocations.
    if args.run_dir is not None:
        run_dir = RunDir.adopt(args.run_dir)
    else:
        run_dir = RunDir(e2e.runs_root, label=f"child-{args.index}")
    gateway = _maybe_gateway(e2e, preview=args.preview)

    report = run_one_session(
        e2e,
        test_home=test_home,
        preview=args.preview,
        bypass_known_words=args.bypass_known_words,
        run_dir=run_dir,
        index=args.index,
        driver=None,  # fresh driver per child
        gateway=gateway,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(dataclasses.asdict(report), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Drain deferred deletes before the process exits (clean teardown).
    _drain_qt_deletes()
    with contextlib.suppress(Exception):
        app.processEvents()

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())

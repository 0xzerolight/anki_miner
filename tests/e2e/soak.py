"""Soak runner for the E2E GUI test harness — the HEADLINE feature.

Mines the same episode several sessions in a row and instruments process/disk/
deck state between sessions to surface a bug that "only appears after several
mining sessions in a row". It composes the prior harness building blocks
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
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.curation import AutoCurationResponder
from tests.e2e.driver import E2EMiningError, E2ETimeout, EpisodeTabDriver
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video
from tests.e2e.fixtures_subtitle import get_test_srt
from tests.e2e.instrumentation import (
    StateSnapshot,
    capture_snapshot,
    detect_divergence,
    diff_snapshots,
)

__all__ = [
    "SessionReport",
    "SoakReport",
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


def run_one_session(
    e2e: E2EConfig,
    *,
    test_home: Path,
    preview: bool,
    bypass_known_words: bool,
    run_dir: RunDir,
    index: int,
    driver: EpisodeTabDriver | None = None,
    gateway: AnkiGateway | None = None,
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
        with contextlib.suppress(Exception):
            driver.teardown()

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
    divergence = detect_divergence(post_snaps, cards_created=cards)

    any_failed = any(not s.ok for s in sessions)
    verdict = "FAIL" if any_failed else divergence.verdict

    config = {
        "deck_name": e2e.deck_name,
        "test_home": str(test_home),
        "ankiconnect_url": e2e.ankiconnect_url,
        "curation_policy": e2e.curation_policy,
        "first_n": e2e.first_n,
        "preview": preview,
        "bypass_known_words": bypass_known_words,
        "sessions_requested": len(sessions),
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


def _maybe_gateway(e2e: E2EConfig, *, preview: bool) -> AnkiGateway | None:
    """Return a ready gateway (deck ensured) for a live process run, else ``None``.

    For preview / bypass runs Anki is not used, so no gateway is built. For a live
    process run, the deck is ensured up front; an unreachable Anki yields ``None``
    (the caller — a test — gates on this and skips).
    """
    if preview:
        return None
    try:
        gateway = AnkiGateway(e2e)
        gateway.ping()
    except AnkiUnreachableError:
        return None
    gateway.ensure_test_deck()
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
) -> SoakReport:
    """Mine ``sessions`` times reusing ONE driver/tab; return a :class:`SoakReport`.

    Reusing the single tab across sessions is what catches widget/worker/QThread/
    memory leaks. Between sessions the driver is town down and Qt deferred-deletes
    are drained so a surviving object is a genuine leak in the next snapshot.

    SAFETY: refuses the real home and wraps the whole run in
    :func:`guard_real_home` so the user's ``~/.anki_miner`` is provably untouched.
    """
    test_home = Path(test_home)
    _assert_safe_home(test_home)

    cfg = build_app_config(e2e, test_home, bypass_known_words=bypass_known_words)
    if not (cfg.dicts_root / "e2e-dict" / "index.sqlite").is_file():
        seed_offline_dict(cfg.dicts_root)

    reports: list[SessionReport] = []
    gateway: AnkiGateway | None = None
    any_failed = False

    with guard_real_home(Path.home() / ".anki_miner"):
        gateway = _maybe_gateway(e2e, preview=preview)
        # ONE driver reused across every session (leak detection depends on it).
        driver = EpisodeTabDriver(cfg, run_dir)
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
                reports.append(report)
                any_failed = any_failed or not report.ok
                # Between-session teardown + Qt deferred-delete drain so leaks
                # surface in the next session's snapshot.
                with contextlib.suppress(Exception):
                    driver.teardown()
                _drain_qt_deletes()
        finally:
            with contextlib.suppress(Exception):
                driver.teardown()
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


def _run_child_session(
    e2e: E2EConfig,
    *,
    test_home: Path,
    index: int,
    preview: bool,
    bypass_known_words: bool,
    child_json: Path,
) -> SessionReport:
    """Spawn one fresh ``--one-session`` subprocess and read back its SessionReport.

    A timeout / non-zero exit / unreadable JSON is recorded as ``ok=False`` with a
    stderr tail rather than raising, so a flaky child does not abort the soak.
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
        str(child_json),
    ]
    if preview:
        cmd.append("--preview")
    if bypass_known_words:
        cmd.append("--bypass-known-words")

    env = {
        **os.environ,
        "ANKI_MINER_HOME": str(test_home),
        "QT_QPA_PLATFORM": "offscreen",
        "ANKI_MINER_KEEP_TEMP": "1",
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
) -> SoakReport:
    """Spawn a fresh subprocess per session; aggregate children + parent disk deltas.

    Each child is a clean process (inherits ``ANKI_MINER_HOME`` pre-import). The
    parent captures DISK/DECK snapshots around each child; the SessionReports in
    the aggregate carry the CHILDREN's own in-process snapshots (read back from
    their JSON). Parent disk deltas per session are recorded in ``config`` context.

    SAFETY: refuses the real home; wraps the run in :func:`guard_real_home`.
    """
    test_home = Path(test_home)
    _assert_safe_home(test_home)

    cfg = build_app_config(e2e, test_home, bypass_known_words=bypass_known_words)
    media_temp = cfg.media_temp_folder
    if not (cfg.dicts_root / "e2e-dict" / "index.sqlite").is_file():
        seed_offline_dict(cfg.dicts_root)

    reports: list[SessionReport] = []
    parent_disk_deltas: list[dict] = []
    any_failed = False

    with guard_real_home(Path.home() / ".anki_miner"):
        gateway = _maybe_gateway(e2e, preview=preview)
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
                )
                reports.append(report)
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

    e2e = E2EConfig(test_home=test_home)
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

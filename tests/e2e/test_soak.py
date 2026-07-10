"""Tests for the soak runner (``tests/e2e/soak.py``) — no live Anki needed.

The soak runner's job is to loop mining sessions and instrument cross-session
state. These tests verify the ORCHESTRATION against a ``FakeAnkiConnect``
loopback server: full process runs (all five phases, real card creation into
the fake) in two modes:

* FAITHFUL multi-session soaks (the primary path: known-words subtraction +
  sentence dedup live, so sessions mine a deterministic 4/4/4 ladder of the
  12-lemma fixture — one word per subtitle line per session),
* BYPASS single-session runs (card-everything, exact 12-word set — the
  tokenizer-regression guard). Bypass is single-session ONLY: card creation is
  stateful, so a repeat identical run dup-skips everything.

Process mode extracts real media, so the pipeline-driving tests carry a
per-test ffmpeg skipif (the pure-logic unit tests below keep running on CI,
which has no ffmpeg). Fake-connecting tests carry ``@pytest.mark.network`` —
the socket tripwire blocks unmarked TCP connects, loopback included.
fugashi/MeCab is required for the real tokenizer, so the whole module skips if
absent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_subtitle import EXPECTED_LEMMAS, SUBTITLE_LINES
from tests.e2e.soak import (
    SessionReport,
    SoakReport,
    _assert_safe_home,
    _build_gateway,
    _child_cmd,
    _prepare_home,
    run_crossprocess_soak,
    run_inprocess_soak,
)

pytest.importorskip("fugashi")

# Cards per faithful session: sentence dedup keeps the first unknown word per
# subtitle line, so each session mines exactly one word per line.
_CARDS_PER_FAITHFUL_SESSION = len(SUBTITLE_LINES)

# Process-mode soaks run real phase-3 media extraction; guard per-test (NOT
# module-level — the _child_cmd/_prepare_home/_build_gateway/_check_gui_state
# unit tests below are ffmpeg-free and must keep running on CI).
_needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for process-mode soaks")


# --------------------------------------------------------------------------
# Unit tests for _child_cmd (pure: no subprocess, no Anki)
# --------------------------------------------------------------------------


def test_child_cmd_forwards_all_four_overrides(tmp_path: Path) -> None:
    """_child_cmd includes --policy, --first-n, --deck, --ankiconnect-url in argv.

    Pure test: given an E2EConfig with non-default values for all four forwarded
    fields, assert each flag and its value appear in the returned argv.
    """
    e2e = E2EConfig(
        test_home=tmp_path / "home",
        curation_policy="first_n",
        first_n=5,
        deck_name="My Custom Deck",
        ankiconnect_url="http://127.0.0.1:9999",
    )
    out = tmp_path / "session.json"

    argv = _child_cmd(
        e2e,
        test_home=tmp_path / "home",
        index=3,
        out=out,
        bypass_known_words=True,
    )

    # All four config flags must be present with the correct values.
    assert "--policy" in argv
    assert argv[argv.index("--policy") + 1] == "first_n"

    assert "--first-n" in argv
    assert argv[argv.index("--first-n") + 1] == "5"

    assert "--deck" in argv
    assert argv[argv.index("--deck") + 1] == "My Custom Deck"

    assert "--ankiconnect-url" in argv
    assert argv[argv.index("--ankiconnect-url") + 1] == "http://127.0.0.1:9999"

    # Sanity: bool flag forwarded correctly.
    assert "--bypass-known-words" in argv

    # --index and --out forwarded.
    assert "--index" in argv
    assert argv[argv.index("--index") + 1] == "3"
    assert "--out" in argv
    assert argv[argv.index("--out") + 1] == str(out)


def test_child_cmd_default_policy_forwarded(tmp_path: Path) -> None:
    """_child_cmd forwards the default 'all' policy correctly (first_n=0 ok)."""
    e2e = E2EConfig(test_home=tmp_path / "home")
    argv = _child_cmd(
        e2e,
        test_home=tmp_path / "home",
        index=0,
        out=tmp_path / "s.json",
        bypass_known_words=False,
    )
    assert "--policy" in argv
    assert argv[argv.index("--policy") + 1] == "all"
    assert "--first-n" in argv
    assert argv[argv.index("--first-n") + 1] == "0"
    assert "--bypass-known-words" not in argv


def test_child_cmd_forwards_run_dir(tmp_path: Path) -> None:
    """_child_cmd includes --run-dir <path> when run_dir is given."""
    e2e = E2EConfig(test_home=tmp_path / "home")
    parent_dir = tmp_path / "parent_run"
    argv = _child_cmd(
        e2e,
        test_home=tmp_path / "home",
        index=0,
        out=tmp_path / "s.json",
        bypass_known_words=False,
        run_dir=parent_dir,
    )
    assert "--run-dir" in argv
    assert argv[argv.index("--run-dir") + 1] == str(parent_dir)


def test_child_cmd_no_run_dir_by_default(tmp_path: Path) -> None:
    """_child_cmd omits --run-dir when run_dir param is not supplied."""
    e2e = E2EConfig(test_home=tmp_path / "home")
    argv = _child_cmd(
        e2e,
        test_home=tmp_path / "home",
        index=0,
        out=tmp_path / "s.json",
        bypass_known_words=False,
    )
    assert "--run-dir" not in argv


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_fake_soak(isolated_home: Path, tmp_path: Path, qtbot, fake_anki) -> None:
    """3-session in-process faithful soak: the deterministic 4/4/4 ladder + PASS.

    Reuses ONE tab across 3 sessions (the leak-hunting path) against the fake.
    Faithful mode makes the known-words machinery live: each session mines one
    word per subtitle line (sentence dedup), the next session subtracts them
    (known_words.db + the deck's cards via the vocab query), so the 12 fixture
    lemmas are mined 4/4/4 with no re-mining. Asserts the full accumulation
    contract: per-session cards, disjoint mined sets whose union is
    EXPECTED_LEMMAS, the known_words.db row ladder, the cross-session
    no-remine invariant, expected divergence deltas, and a PASS verdict.
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="inproc")

    soak = run_inprocess_soak(
        e2e,
        sessions=3,
        bypass_known_words=False,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert isinstance(soak, SoakReport)
    assert soak.mode == "inprocess"
    assert len(soak.sessions) == 3
    per = _CARDS_PER_FAITHFUL_SESSION
    for s in soak.sessions:
        assert isinstance(s, SessionReport)
        assert s.ok, s.errors
        assert s.words_found > 0
        assert s.cards_created == per
        assert s.delta, "per-session delta should be populated"
        assert s.snapshot_pre is not None and s.snapshot_post is not None
        shot = run_dir.path / s.screenshot
        assert s.screenshot and shot.is_file() and shot.stat().st_size > 0

    # The 4/4/4 ladder: disjoint per-session mined sets covering all 12 lemmas.
    mined = [set(s.mined_forms) for s in soak.sessions]
    assert all(len(m) == per for m in mined)
    assert sum(len(m) for m in mined) == len(EXPECTED_LEMMAS)  # pairwise disjoint
    assert set().union(*mined) == set(EXPECTED_LEMMAS)

    # known_words.db accumulates one session's worth of rows per session.
    assert [s.known_words_count for s in soak.sessions] == [per, per * 2, per * 3]

    # Cross-session no-remine invariant (set on every session but the last).
    for s in soak.sessions[:-1]:
        assert s.known_words_not_remined is True, s.errors
    assert soak.sessions[-1].known_words_not_remined is None

    # Expected-grower deltas: last-minus-first over post-session snapshots.
    deltas = soak.divergence["expected_deltas"]
    assert deltas["known_words.db"] == per * 2
    assert deltas["anki_test_deck_count"] == per * 2

    assert soak.divergence.get("verdict") == "PASS"
    assert soak.verdict == "PASS"

    report_path = run_dir.path / "report.json"
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["mode"] == "inprocess"
    assert len(loaded["sessions"]) == 3


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_fake_soak_full_window(isolated_home: Path, tmp_path: Path, qtbot, fake_anki) -> None:
    """2-session full-window faithful soak: a real MainWindow drives each session.

    The opt-in ``full_window=True`` path builds an ``AppDriver`` (real
    ``MainWindow`` with the episode tab mounted + dialogs patched) and reuses it
    across sessions against the fake. Asserts every session completed ok with
    one card per subtitle line and the soak did not FAIL — i.e. the
    full-window driver mines and disposes cleanly across sessions (no
    leak/freeze). A WARN is tolerated ONLY for the RSS-slope heuristic:
    process-mode sessions warm real caches (ffmpeg buffers, the requests
    connection pool, media stores), so a 2-point slope is dominated by
    first-session warmup and can brush the 5 MB/session threshold without any
    leak; every other flag (widgets, threads, sqlite, stalls) still fails.
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="inproc-fw")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        bypass_known_words=False,
        run_dir=run_dir,
        test_home=isolated_home,
        full_window=True,
    )

    assert isinstance(soak, SoakReport)
    assert len(soak.sessions) == 2
    for s in soak.sessions:
        assert s.ok, s.errors
        assert s.words_found > 0
        assert s.cards_created == _CARDS_PER_FAITHFUL_SESSION
    assert soak.verdict in ("PASS", "WARN")
    if soak.verdict == "WARN":
        flags = soak.divergence.get("flags", [])
        assert flags and all("RSS" in f for f in flags), f"non-RSS WARN flags: {flags}"


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_soak_inject_cancel_appends_dedicated_session(
    isolated_home: Path, tmp_path: Path, qtbot, fake_anki
) -> None:
    """--inject-cancel appends ONE dedicated cancel session that ends cleanly.

    A 2-session faithful soak plus an injected cancel yields 3 SessionReports;
    the last is the cancel session. It must record a cancel outcome (worker
    joined + tab idle) and be ``ok`` (run ended promptly, no leaked thread /
    stuck UI). The normal sessions remain unaffected (the cancel is its own
    session).
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="cancel-soak")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        bypass_known_words=False,
        run_dir=run_dir,
        test_home=isolated_home,
        inject_cancel=0.0,
    )

    assert len(soak.sessions) == 3  # 2 normal + 1 dedicated cancel
    normal, cancel = soak.sessions[:2], soak.sessions[2]
    for s in normal:
        assert s.ok, s.errors
        assert not s.cancel_outcome  # normal sessions never cancel

    # The dedicated cancel session ended cleanly and is recorded.
    assert cancel.cancel_outcome, "cancel session must record a cancel_outcome"
    assert cancel.cancel_outcome["joined"] is True
    assert cancel.cancel_outcome["buttons_idle"] is True
    assert cancel.ok, cancel.errors

    # report.json round-trips the cancel_outcome.
    loaded = json.loads((run_dir.path / "report.json").read_text(encoding="utf-8"))
    assert loaded["sessions"][2]["cancel_outcome"]["joined"] is True


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_fake_soak_gui_checks_populated_and_pass(
    isolated_home: Path, tmp_path: Path, qtbot, fake_anki
) -> None:
    """GUI-state checks are recorded in every SessionReport and all pass for a healthy run.

    After each session ``run_one_session`` calls ``_check_gui_state`` and stores
    the result in ``SessionReport.gui_checks``.  For a healthy process run
    every check must be ``ok=True`` (buttons idle, log non-empty, phase markers
    through Step 5/5 present, progress advanced).  A failing check would also
    set ``session.ok=False`` which would make the overall soak ``FAIL`` —
    verified by the verdict assertion.
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="gui-checks")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        bypass_known_words=False,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert soak.verdict == "PASS", f"soak verdict FAIL — session errors: {[s.errors for s in soak.sessions]}"
    for s in soak.sessions:
        assert s.ok, f"session {s.index} failed: {s.errors}"
        assert s.gui_checks, f"session {s.index}: gui_checks dict is empty (not populated)"
        failed = {name: c for name, c in s.gui_checks.items() if not c["ok"]}
        assert not failed, f"session {s.index}: GUI checks failed: " + ", ".join(
            f"{n}(expected={c['expected']!r} actual={c['actual']!r})" for n, c in failed.items()
        )
        # Verify the expected check keys are present.
        assert "buttons_idle" in s.gui_checks
        assert "log_nonempty" in s.gui_checks
        assert "log_contains:Step 1/5" in s.gui_checks
        assert "log_contains:Step 2/5" in s.gui_checks
        # Phase markers recorded in order when all common markers found.
        assert "log_markers_in_order" in s.gui_checks
        # Cards were created every session, so the phase-5 marker is asserted.
        assert "log_contains:Step 5/5" in s.gui_checks
        assert s.gui_checks["log_contains:Step 5/5"]["ok"] is True
        # Progress state is ALWAYS recorded as data.
        assert "progress_value" in s.gui_checks, "progress_value must be recorded in gui_checks"
        assert "progress_text" in s.gui_checks, "progress_text must be recorded in gui_checks"
        assert isinstance(s.gui_checks["progress_value"]["actual"], int)
        assert isinstance(s.gui_checks["progress_text"]["actual"], str)
        # Process mode advances progress: the stuck check fired and passed.
        assert "progress_not_stuck" in s.gui_checks
        assert s.gui_checks["progress_not_stuck"]["actual"] is False  # not stuck
        assert s.gui_checks["progress_value"]["actual"] > 0


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_fake_soak_temp_files_stable(isolated_home: Path, tmp_path: Path, qtbot, fake_anki) -> None:
    """temp_files delta is 0 across sessions when ANKI_MINER_KEEP_TEMP is not set.

    The harness no longer forces ANKI_MINER_KEEP_TEMP, so the processor cleans
    temp after each session. A healthy soak must show zero temp_files growth
    between sessions — confirming ``temp_files`` is a real leak signal (process
    mode creates real temp media, so this check now has teeth).
    """
    import os

    # Guarantee ANKI_MINER_KEEP_TEMP is unset for this test.
    env_backup = os.environ.pop("ANKI_MINER_KEEP_TEMP", None)
    try:
        e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
        run_dir = RunDir(tmp_path / "runs", label="temp-stable")

        soak = run_inprocess_soak(
            e2e,
            sessions=2,
            bypass_known_words=False,
            run_dir=run_dir,
            test_home=isolated_home,
        )

        assert soak.verdict == "PASS"
        for s in soak.sessions:
            assert s.ok, s.errors
            # temp_files should not accumulate — delta must be <= 0 per session.
            temp_delta = s.delta.get("temp_files", 0)
            assert temp_delta <= 0, (
                f"Session {s.index}: temp_files grew by {temp_delta} "
                "(ANKI_MINER_KEEP_TEMP must not be forced by the harness)"
            )
    finally:
        if env_backup is not None:
            os.environ["ANKI_MINER_KEEP_TEMP"] = env_backup


@pytest.mark.network
@_needs_ffmpeg
def test_crossprocess_fake_soak(isolated_home: Path, tmp_path: Path, fake_anki) -> None:
    """2-session cross-process faithful soak: real subprocesses aggregated + report.

    Spawns ``python -m tests.e2e.soak --one-session`` children (offscreen)
    against the fake — a real TCP server, so the children reach it over
    ``--ankiconnect-url`` forwarding. Asserts 2 session reports aggregated
    (one card per subtitle line each), child JSONs produced, and the report
    written.
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="crossproc")

    soak = run_crossprocess_soak(
        e2e,
        sessions=2,
        bypass_known_words=False,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert soak.mode == "crossprocess"
    assert len(soak.sessions) == 2
    # Children should have run cleanly offscreen; surface their errors if not.
    for s in soak.sessions:
        assert s.ok, s.errors
        assert s.words_found > 0
        assert s.cards_created == _CARDS_PER_FAITHFUL_SESSION
        assert s.snapshot_post is not None

    # Each session's screenshot must resolve under the PARENT run dir (not a child
    # subdir). This is the core fix: child artifacts are co-located with the parent.
    for s in soak.sessions:
        assert s.screenshot, f"session {s.index} has no screenshot name"
        shot_path = run_dir.path / s.screenshot
        assert shot_path.is_file() and shot_path.stat().st_size > 0, (
            f"session {s.index} screenshot '{s.screenshot}' not found under " f"parent run dir {run_dir.path}"
        )

    # Child handoff JSONs exist on disk.
    for i in range(2):
        assert (run_dir.path / f"child_session_{i}.json").is_file()

    report_path = run_dir.path / "report.json"
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["mode"] == "crossprocess"
    assert len(loaded["sessions"]) == 2
    # Parent disk deltas recorded as cross-process context.
    assert "parent_disk_deltas" in loaded["config"]


# --------------------------------------------------------------------------
# _prepare_home / --fresh-home unit tests (no Anki, no Qt)
# --------------------------------------------------------------------------


def test_prepare_home_fresh_clears_existing_contents(tmp_path: Path) -> None:
    """_prepare_home(fresh=True) empties a pre-existing home dir."""
    home = tmp_path / "e2e_home"
    home.mkdir()
    (home / "known_words.db").write_text("not a real db")
    (home / "subdir").mkdir()
    (home / "subdir" / "file.txt").write_text("hello")

    result = _prepare_home(home, fresh=True)

    # Dir still exists but its contents are gone.
    assert home.is_dir()
    assert list(home.iterdir()) == [], "fresh=True must empty the home dir"

    # Baseline captured BEFORE the wipe.
    assert result["home_pre_existed"] is True
    assert "home_baseline" in result


def test_prepare_home_fresh_preserves_runs_dir(tmp_path: Path) -> None:
    """_prepare_home(fresh=True) keeps the runs/ subdir (this run's RunDir lives there).

    Regression guard: the runner creates the RunDir under ``test_home/runs``
    BEFORE the soak calls _prepare_home; wiping it stranded report.json and
    screenshot writes on a deleted directory (surfaced by --fake-anki, which
    forces fresh_home).
    """
    home = tmp_path / "e2e_home"
    home.mkdir()
    (home / "known_words.db").write_text("stale state — must go")
    run_dir = home / "runs" / "20260101_000000_soak"
    run_dir.mkdir(parents=True)
    (run_dir / "01_shot.png").write_text("artifact — must stay")

    _prepare_home(home, fresh=True)

    assert not (home / "known_words.db").exists(), "home state must be wiped"
    assert (run_dir / "01_shot.png").is_file(), "runs/ artifacts must survive the wipe"


def test_prepare_home_no_fresh_leaves_existing_contents(tmp_path: Path) -> None:
    """_prepare_home(fresh=False) does NOT remove existing files."""
    home = tmp_path / "e2e_home"
    home.mkdir()
    sentinel = home / "sentinel.txt"
    sentinel.write_text("keep me")

    _prepare_home(home, fresh=False)

    assert sentinel.is_file(), "fresh=False must not delete anything"


def test_prepare_home_records_pre_existed_false_for_new_home(tmp_path: Path) -> None:
    """_prepare_home records home_pre_existed=False when home didn't exist yet."""
    home = tmp_path / "brand_new_home"
    assert not home.exists()

    result = _prepare_home(home, fresh=False)

    assert result["home_pre_existed"] is False
    assert result["home_baseline"] == {"known_words_rows": 0, "temp_files": 0}
    assert home.is_dir()


def test_prepare_home_records_pre_existed_true_for_existing_home(tmp_path: Path) -> None:
    """_prepare_home records home_pre_existed=True when home already existed."""
    home = tmp_path / "existing_home"
    home.mkdir()

    result = _prepare_home(home, fresh=False)

    assert result["home_pre_existed"] is True


def test_prepare_home_refuses_real_home() -> None:
    """_prepare_home refuses the real ~/.anki_miner via _assert_safe_home."""
    real = Path.home() / ".anki_miner"
    with pytest.raises(AssertionError, match="Refusing to run"):
        _prepare_home(real, fresh=False)


def test_assert_safe_home_refuses_real_home() -> None:
    """_assert_safe_home raises AssertionError for the real home path."""
    real = Path.home() / ".anki_miner"
    with pytest.raises(AssertionError, match="Refusing to run"):
        _assert_safe_home(real)


def test_prepare_home_fresh_true_refuses_real_home_and_preserves_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_prepare_home(fresh=True) raises BEFORE rmtree when path == real ~/.anki_miner.

    Monkeypatches Path.home() so that (Path.home() / ".anki_miner") resolves to
    a tmp dir we control — the real user home is never touched.  A sentinel file
    is seeded inside that fake "real home"; the test confirms _assert_safe_home
    fires (AssertionError) AND that the sentinel still exists afterward (i.e.
    shutil.rmtree was NOT reached).
    """
    fake_user_home = tmp_path / "fake_home"
    fake_user_home.mkdir()
    fake_real_anki = fake_user_home / ".anki_miner"
    fake_real_anki.mkdir()
    sentinel = fake_real_anki / "sentinel.txt"
    sentinel.write_text("must survive")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_user_home))

    with pytest.raises(AssertionError, match="Refusing to run"):
        _prepare_home(fake_real_anki, fresh=True)

    assert sentinel.exists(), "guard fired after rmtree — sentinel was deleted"


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_soak_fresh_home_records_baseline_in_report(
    isolated_home: Path, tmp_path: Path, qtbot, fake_anki
) -> None:
    """run_inprocess_soak with fresh_home=True records home_pre_existed + baseline in report."""
    # Seed a file so the pre-existed=True path is exercised.
    (isolated_home / "sentinel.txt").write_text("stale")

    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="fresh")

    soak = run_inprocess_soak(
        e2e,
        sessions=1,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
        fresh_home=True,
    )

    assert "home_pre_existed" in soak.config
    assert soak.config["home_pre_existed"] is True
    assert "home_baseline" in soak.config
    assert isinstance(soak.config["home_baseline"], dict)
    assert "known_words_rows" in soak.config["home_baseline"]
    assert "temp_files" in soak.config["home_baseline"]

    # The sentinel was wiped before the run.
    assert not (isolated_home / "sentinel.txt").exists()


@pytest.mark.network
@_needs_ffmpeg
def test_inprocess_soak_no_fresh_home_leaves_files(isolated_home: Path, tmp_path: Path, qtbot, fake_anki) -> None:
    """run_inprocess_soak with fresh_home=False does NOT delete the test home contents."""
    sentinel = isolated_home / "sentinel.txt"
    sentinel.write_text("keep me")

    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="no-fresh")

    run_inprocess_soak(
        e2e,
        sessions=1,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
        fresh_home=False,
    )

    assert sentinel.is_file(), "fresh_home=False must not remove pre-existing files"


# --------------------------------------------------------------------------
# Mined word-set assertions in the soak loop (Task 13)
# --------------------------------------------------------------------------


@pytest.mark.network
@_needs_ffmpeg
def test_session_report_mined_forms_exact_in_bypass_single_run(
    isolated_home: Path, tmp_path: Path, qtbot, fake_anki
) -> None:
    """SessionReport.mined_forms == set(EXPECTED_LEMMAS) in a single bypass run.

    A tokenizer regression that changes WHICH words are mined (same count, wrong
    set) would set session.ok=False + a descriptive error — caught before it
    reaches production. Single-session ONLY: bypass card creation is stateful,
    so a second identical session would dup-skip everything (the runner rejects
    multi-session bypass for the same reason).
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="mined-set-soak")

    soak = run_inprocess_soak(
        e2e,
        sessions=1,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert soak.verdict == "PASS", f"soak FAIL: {[s.errors for s in soak.sessions]}"
    for s in soak.sessions:
        assert s.ok, f"session {s.index} failed: {s.errors}"
        # mined_forms field is populated.
        assert s.mined_forms, f"session {s.index}: mined_forms is empty"
        # Every word became a card in bypass mode (dedup off, card-everything).
        assert s.cards_created == len(EXPECTED_LEMMAS)
        # Set matches EXPECTED_LEMMAS exactly in bypass mode.
        assert set(s.mined_forms) == set(EXPECTED_LEMMAS), (
            f"session {s.index} mined-set mismatch:\n"
            f"  observed={sorted(s.mined_forms)}\n"
            f"  expected={sorted(EXPECTED_LEMMAS)}\n"
            f"  extra={sorted(set(s.mined_forms) - set(EXPECTED_LEMMAS))}\n"
            f"  missing={sorted(set(EXPECTED_LEMMAS) - set(s.mined_forms))}"
        )


@pytest.mark.network
@_needs_ffmpeg
def test_session_report_mined_forms_in_report_json(isolated_home: Path, tmp_path: Path, qtbot, fake_anki) -> None:
    """mined_forms is serialised into report.json and round-trips cleanly.

    Verifies the field is JSON-friendly (list[str]) so report.json always
    carries the mined set for post-hoc inspection.
    """
    e2e = E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url)
    run_dir = RunDir(tmp_path / "runs", label="mined-json")

    run_inprocess_soak(
        e2e,
        sessions=1,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    loaded = json.loads((run_dir.path / "report.json").read_text(encoding="utf-8"))
    session = loaded["sessions"][0]
    assert "mined_forms" in session, "mined_forms key absent from report.json session"
    assert isinstance(session["mined_forms"], list)
    assert set(session["mined_forms"]) == set(EXPECTED_LEMMAS)


@pytest.mark.e2e
@pytest.mark.soak
def test_live_process_soak_skips_when_anki_down(isolated_home: Path, tmp_path: Path) -> None:
    """A live (Process, faithful) soak skips cleanly when Anki is unreachable.

    In this environment Anki is down, so the gateway ping raises and the test
    skips — proving the live path is gated, not erroring.
    """
    e2e = E2EConfig(test_home=isolated_home)
    try:
        AnkiGateway(e2e).ping()
    except AnkiUnreachableError:
        pytest.skip("Anki not running (AnkiConnect unreachable)")

    # Reached only with Anki up: run a 2-session live faithful in-process soak
    # (bypass is single-session only — card creation is stateful).
    run_dir = RunDir(tmp_path / "runs", label="live")
    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        bypass_known_words=False,
        run_dir=run_dir,
        test_home=isolated_home,
    )
    assert len(soak.sessions) == 2
    assert (run_dir.path / "report.json").is_file()


# --------------------------------------------------------------------------
# Unit tests for _build_gateway (no live Anki — gateway/post_action mocked)
# --------------------------------------------------------------------------

_SOAK_GW = "tests.e2e.soak.AnkiGateway"


def test_build_gateway_raises_when_anki_down(tmp_path: Path) -> None:
    """_build_gateway re-raises AnkiUnreachableError so the runner's exit-2 handler fires."""
    e2e = E2EConfig(test_home=tmp_path)
    with patch(_SOAK_GW) as mock_gw_cls:
        mock_instance = mock_gw_cls.return_value
        mock_instance.ping.side_effect = AnkiUnreachableError("connection refused")
        with pytest.raises(AnkiUnreachableError):
            _build_gateway(e2e)


def test_build_gateway_returns_gateway_when_anki_up(tmp_path: Path) -> None:
    """_build_gateway returns the pinged, deck- and model-ensured gateway."""
    e2e = E2EConfig(test_home=tmp_path)
    with patch(_SOAK_GW) as mock_gw_cls:
        mock_instance = mock_gw_cls.return_value
        mock_instance.ping.return_value = None
        result = _build_gateway(e2e)
    assert result is mock_instance
    mock_instance.ping.assert_called_once()
    mock_instance.ensure_test_deck.assert_called_once_with(allow_existing=False)
    mock_instance.ensure_test_model.assert_called_once()


def test_build_gateway_adopt_deck_allows_existing(tmp_path: Path) -> None:
    """adopt_deck=True forwards allow_existing=True (the cross-process child path).

    Guards the ForeignDeckError fix: a child's fresh gateway must adopt the deck
    the parent/earlier sessions already populated.
    """
    e2e = E2EConfig(test_home=tmp_path)
    with patch(_SOAK_GW) as mock_gw_cls:
        mock_instance = mock_gw_cls.return_value
        mock_instance.ping.return_value = None
        _build_gateway(e2e, adopt_deck=True)
    mock_instance.ensure_test_deck.assert_called_once_with(allow_existing=True)


# --------------------------------------------------------------------------
# Unit tests for _check_gui_state — cards_created gating (no Qt required)
# --------------------------------------------------------------------------


def _make_mock_driver(
    *,
    buttons_idle: bool = True,
    log: str = "Step 1/5\nStep 2/5\n",
    progress_value: int = 0,
    progress_text: str = "",
) -> object:
    """Build a minimal stub for EpisodeTabDriver / AppDriver interface.

    _check_gui_state only calls: buttons_idle(), log_text(), progress_value(),
    progress_text().  Returns a plain object with those methods.
    """
    from unittest.mock import MagicMock

    driver = MagicMock()
    driver.buttons_idle.return_value = buttons_idle
    driver.log_text.return_value = log
    driver.progress_value.return_value = progress_value
    driver.progress_text.return_value = progress_text
    return driver


def _call_check_gui_state(**kwargs):
    """Call _check_gui_state with _drain_until patched to a no-op."""
    from tests.e2e.soak import _check_gui_state

    with patch("tests.e2e.driver._drain_until"):
        return _check_gui_state(**kwargs)


def test_check_gui_state_process_cards_created_zero_no_step5_passes() -> None:
    """Process run, cards_created==0, log has Step 1/5+2/5 but NOT Step 5/5 → PASS.

    Models the faithful soak early-return path: all words already known, pipeline
    returns before phase 5, progress never advances.  Both log_contains:Step 5/5
    and progress_not_stuck must be ok=True (data-only, never a failure).
    """
    log = "Step 1/5\nStep 2/5\nAll words already in Anki!\n"
    driver = _make_mock_driver(log=log, progress_value=0, progress_text="")
    checks = _call_check_gui_state(
        driver=driver,
        result_ok=True,
        cards_created=0,
    )

    # Step 5/5 absent from log but ok=True because cards_created==0.
    step5 = checks.get("log_contains:Step 5/5")
    assert step5 is not None, "log_contains:Step 5/5 must be recorded"
    assert step5["actual"] is False, "Step 5/5 should not be in log"
    assert step5["ok"] is True, "ok must be True when cards_created==0 (early return)"

    # progress_not_stuck recorded as data-only (ok=True) even though stuck.
    stuck_check = checks.get("progress_not_stuck")
    assert stuck_check is not None, "progress_not_stuck must be recorded"
    assert stuck_check["actual"] is True, "progress IS stuck (value=0, idle status)"
    assert stuck_check["ok"] is True, "ok must be True when cards_created==0"

    # All other checks pass (buttons idle, log non-empty, Step 1/5 + 2/5 present).
    failed = [n for n, c in checks.items() if not c["ok"]]
    assert not failed, f"unexpected failures: {failed}"


def test_check_gui_state_process_cards_created_positive_missing_step5_fails() -> None:
    """Process run, cards_created>0, log MISSING Step 5/5 → FAIL.

    Regression guard: a genuine bug (cards created but phase-5 marker absent)
    must still be caught.  ok must be False for log_contains:Step 5/5.
    """
    log = "Step 1/5\nStep 2/5\n"  # Step 5/5 deliberately absent
    driver = _make_mock_driver(log=log, progress_value=50, progress_text="Processing")
    checks = _call_check_gui_state(
        driver=driver,
        result_ok=True,
        cards_created=5,
    )

    step5 = checks.get("log_contains:Step 5/5")
    assert step5 is not None
    assert step5["actual"] is False
    assert step5["ok"] is False, "ok must be False when cards_created>0 and Step 5/5 absent"


def test_check_gui_state_process_cards_created_positive_stuck_progress_fails() -> None:
    """Process run, cards_created>0, progress stuck (value=0 + idle status) → FAIL.

    Regression guard: a genuine stuck-progress bug (cards created but progress
    never advanced) must still be caught.  ok must be False for progress_not_stuck.
    """
    log = "Step 1/5\nStep 2/5\nStep 5/5\n"
    driver = _make_mock_driver(log=log, progress_value=0, progress_text="")
    checks = _call_check_gui_state(
        driver=driver,
        result_ok=True,
        cards_created=5,
    )

    stuck_check = checks.get("progress_not_stuck")
    assert stuck_check is not None
    assert stuck_check["actual"] is True, "progress IS stuck"
    assert stuck_check["ok"] is False, "ok must be False when cards_created>0 and progress stuck"


def test_check_gui_state_process_cards_created_positive_healthy_passes() -> None:
    """Process run, cards_created>0, Step 5/5 present, progress advanced → PASS.

    The happy path (smoke / bypass mode): cards were created, phase 5 ran,
    progress advanced beyond 0.  All checks must be ok=True.
    """
    log = "Step 1/5\nStep 2/5\nStep 5/5\n"
    driver = _make_mock_driver(log=log, progress_value=100, progress_text="Complete")
    checks = _call_check_gui_state(
        driver=driver,
        result_ok=True,
        cards_created=12,
    )

    failed = [n for n, c in checks.items() if not c["ok"]]
    assert not failed, f"unexpected failures in healthy process run: {failed}"

    step5 = checks["log_contains:Step 5/5"]
    assert step5["ok"] is True and step5["actual"] is True

    stuck_check = checks["progress_not_stuck"]
    assert stuck_check["ok"] is True and stuck_check["actual"] is False

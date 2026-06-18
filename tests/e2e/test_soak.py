"""Tests for the soak runner (``tests/e2e/soak.py``) — no Anki, preview mode.

The soak runner's job is to loop mining sessions and instrument cross-session
state. These tests verify the ORCHESTRATION without a live Anki by using PREVIEW
mode + ``bypass_known_words=True`` (the offscreen, deterministic, no-AnkiConnect
path — see ``app_config`` docstring):

* in-process preview soak (the primary path: one tab reused across sessions),
* cross-process preview soak (real ``--one-session`` subprocesses),
* a live process soak that SKIPS cleanly when Anki is down (the env here).

fugashi/MeCab is required for the real tokenizer, so the whole module skips if
absent. Preview needs no ffmpeg/Anki.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_subtitle import EXPECTED_LEMMAS
from tests.e2e.soak import (
    SessionReport,
    SoakReport,
    _assert_safe_home,
    _child_cmd,
    _maybe_gateway,
    _prepare_home,
    run_crossprocess_soak,
    run_inprocess_soak,
)

pytest.importorskip("fugashi")


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
        preview=True,
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

    # Sanity: bool flags forwarded correctly.
    assert "--preview" in argv
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
        preview=False,
        bypass_known_words=False,
    )
    assert "--policy" in argv
    assert argv[argv.index("--policy") + 1] == "all"
    assert "--first-n" in argv
    assert argv[argv.index("--first-n") + 1] == "0"
    assert "--preview" not in argv
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
        preview=False,
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
        preview=False,
        bypass_known_words=False,
    )
    assert "--run-dir" not in argv


def test_inprocess_preview_soak(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """3-session in-process preview soak: every session ok, report written + PASS.

    Reuses ONE tab across 3 sessions (the leak-hunting path). Preview +
    bypass_known_words runs fully offscreen. Asserts each session found words and
    has a screenshot + delta, the divergence verdict is PASS (preview shouldn't
    leak across 3 sessions), and ``report.json`` is JSON-loadable.
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="inproc")

    soak = run_inprocess_soak(
        e2e,
        sessions=3,
        preview=True,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert isinstance(soak, SoakReport)
    assert soak.mode == "inprocess"
    assert len(soak.sessions) == 3
    for s in soak.sessions:
        assert isinstance(s, SessionReport)
        assert s.ok, s.errors
        assert s.words_found > 0
        assert s.cards_created == 0  # preview creates nothing
        assert s.delta, "per-session delta should be populated"
        assert s.snapshot_pre is not None and s.snapshot_post is not None
        shot = run_dir.path / s.screenshot
        assert s.screenshot and shot.is_file() and shot.stat().st_size > 0

    # Divergence verdict present; preview shouldn't leak across 3 sessions.
    assert soak.divergence.get("verdict") == "PASS"
    assert soak.verdict == "PASS"

    report_path = run_dir.path / "report.json"
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["mode"] == "inprocess"
    assert len(loaded["sessions"]) == 3


def test_inprocess_preview_soak_full_window(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """2-session full-window preview soak: a real MainWindow drives each session.

    The opt-in ``full_window=True`` path builds an ``AppDriver`` (real
    ``MainWindow`` with the episode tab mounted + dialogs patched) and reuses it
    across sessions. Preview + bypass runs fully offscreen. Asserts every session
    completed ok with words found and the soak verdict is PASS — i.e. the
    full-window driver mines and disposes cleanly across sessions (no
    leak/freeze).
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="inproc-fw")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        preview=True,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
        full_window=True,
    )

    assert isinstance(soak, SoakReport)
    assert len(soak.sessions) == 2
    for s in soak.sessions:
        assert s.ok, s.errors
        assert s.words_found > 0
        assert s.cards_created == 0  # preview creates nothing
    assert soak.verdict == "PASS"


def test_inprocess_soak_inject_cancel_appends_dedicated_session(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """--inject-cancel appends ONE dedicated cancel session that ends cleanly.

    A 2-session preview soak plus an injected cancel yields 3 SessionReports; the
    last is the cancel session. It must record a cancel outcome (worker joined +
    tab idle) and be ``ok`` (run ended promptly, no leaked thread / stuck UI). The
    normal sessions remain unaffected (the cancel is its own session).
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="cancel-soak")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        preview=True,
        bypass_known_words=True,
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


def test_inprocess_preview_soak_gui_checks_populated_and_pass(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """GUI-state checks are recorded in every SessionReport and all pass for a healthy preview run.

    After each session ``run_one_session`` calls ``_check_gui_state`` and stores
    the result in ``SessionReport.gui_checks``.  For a healthy offscreen preview
    every check must be ``ok=True`` (buttons idle, log non-empty, phase markers
    present).  A failing check would also set ``session.ok=False`` which would
    make the overall soak ``FAIL`` — verified by the verdict assertion.

    Progress state (``progress_value`` / ``progress_text``) is recorded as data
    in both modes — this test verifies those keys exist even in preview mode where
    the progress bar is not advanced (value stays 0 by design).  No ok=False
    assertion is made for progress in preview mode.
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="gui-checks")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        preview=True,
        bypass_known_words=True,
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
        # Preview: process-only marker must NOT be in checks.
        assert "log_contains:Step 5/5" not in s.gui_checks
        # Progress state is ALWAYS recorded as data (both modes).
        assert "progress_value" in s.gui_checks, "progress_value must be recorded in gui_checks"
        assert "progress_text" in s.gui_checks, "progress_text must be recorded in gui_checks"
        # In preview mode progress_value stays 0 (callback not invoked) — that's expected.
        # The key point is that the value IS recorded (not absent).
        assert isinstance(s.gui_checks["progress_value"]["actual"], int)
        assert isinstance(s.gui_checks["progress_text"]["actual"], str)
        # Preview: no stuck-progress assertion (progress_not_stuck absent in preview).
        assert "progress_not_stuck" not in s.gui_checks


def test_inprocess_preview_soak_temp_files_stable(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """temp_files delta is 0 across sessions when ANKI_MINER_KEEP_TEMP is not set.

    The harness no longer forces ANKI_MINER_KEEP_TEMP, so the processor cleans
    temp after each session. A healthy preview soak must show zero temp_files
    growth between sessions — confirming ``temp_files`` is a real leak signal.
    """
    import os

    # Guarantee ANKI_MINER_KEEP_TEMP is unset for this test.
    env_backup = os.environ.pop("ANKI_MINER_KEEP_TEMP", None)
    try:
        e2e = E2EConfig(test_home=isolated_home)
        run_dir = RunDir(tmp_path / "runs", label="temp-stable")

        soak = run_inprocess_soak(
            e2e,
            sessions=2,
            preview=True,
            bypass_known_words=True,
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


def test_crossprocess_preview_soak(isolated_home: Path, tmp_path: Path) -> None:
    """2-session cross-process preview soak: real subprocesses aggregated + report.

    Spawns ``python -m tests.e2e.soak --one-session`` children (offscreen, no
    Anki). Asserts 2 session reports aggregated, child JSONs produced, and the
    report written.
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="crossproc")

    soak = run_crossprocess_soak(
        e2e,
        sessions=2,
        preview=True,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert soak.mode == "crossprocess"
    assert len(soak.sessions) == 2
    # Children should have run cleanly offscreen; surface their errors if not.
    for s in soak.sessions:
        assert s.ok, s.errors
        assert s.words_found > 0
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


def test_inprocess_soak_fresh_home_records_baseline_in_report(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """run_inprocess_soak with fresh_home=True records home_pre_existed + baseline in report."""
    # Seed a file so the pre-existed=True path is exercised.
    (isolated_home / "sentinel.txt").write_text("stale")

    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="fresh")

    soak = run_inprocess_soak(
        e2e,
        sessions=1,
        preview=True,
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


def test_inprocess_soak_no_fresh_home_leaves_files(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """run_inprocess_soak with fresh_home=False does NOT delete the test home contents."""
    sentinel = isolated_home / "sentinel.txt"
    sentinel.write_text("keep me")

    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="faithful")

    run_inprocess_soak(
        e2e,
        sessions=1,
        preview=True,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
        fresh_home=False,
    )

    assert sentinel.is_file(), "fresh_home=False must not remove pre-existing files"


# --------------------------------------------------------------------------
# Mined word-set assertions in the soak loop (Task 13)
# --------------------------------------------------------------------------


def test_session_report_records_mined_forms_in_bypass_soak(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """Each SessionReport.mined_forms == set(EXPECTED_LEMMAS) in bypass preview soak.

    A tokenizer regression that changes WHICH words are mined (same count, wrong
    set) would set session.ok=False + a descriptive error — caught before it
    reaches production.
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="mined-set-soak")

    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        preview=True,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
    )

    assert soak.verdict == "PASS", f"soak FAIL: {[s.errors for s in soak.sessions]}"
    for s in soak.sessions:
        assert s.ok, f"session {s.index} failed: {s.errors}"
        # mined_forms field is populated.
        assert s.mined_forms, f"session {s.index}: mined_forms is empty"
        # Set matches EXPECTED_LEMMAS exactly in bypass mode.
        assert set(s.mined_forms) == set(EXPECTED_LEMMAS), (
            f"session {s.index} mined-set mismatch:\n"
            f"  observed={sorted(s.mined_forms)}\n"
            f"  expected={sorted(EXPECTED_LEMMAS)}\n"
            f"  extra={sorted(set(s.mined_forms) - set(EXPECTED_LEMMAS))}\n"
            f"  missing={sorted(set(EXPECTED_LEMMAS) - set(s.mined_forms))}"
        )


def test_session_report_mined_forms_in_report_json(isolated_home: Path, tmp_path: Path, qtbot) -> None:
    """mined_forms is serialised into report.json and round-trips cleanly.

    Verifies the new field is JSON-friendly (list[str]) so report.json always
    carries the mined set for post-hoc inspection.
    """
    e2e = E2EConfig(test_home=isolated_home)
    run_dir = RunDir(tmp_path / "runs", label="mined-json")

    run_inprocess_soak(
        e2e,
        sessions=1,
        preview=True,
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

    # Reached only with Anki up: run a 2-session live in-process soak.
    run_dir = RunDir(tmp_path / "runs", label="live")
    soak = run_inprocess_soak(
        e2e,
        sessions=2,
        preview=False,
        bypass_known_words=True,
        run_dir=run_dir,
        test_home=isolated_home,
    )
    assert len(soak.sessions) == 2
    assert (run_dir.path / "report.json").is_file()


# --------------------------------------------------------------------------
# Unit tests for _maybe_gateway (no live Anki — gateway/post_action mocked)
# --------------------------------------------------------------------------

_SOAK_GW = "tests.e2e.soak.AnkiGateway"


def test_maybe_gateway_preview_returns_none_without_pinging(tmp_path: Path) -> None:
    """preview=True returns None immediately — AnkiGateway is never constructed."""
    e2e = E2EConfig(test_home=tmp_path)
    with patch(_SOAK_GW) as mock_gw_cls:
        result = _maybe_gateway(e2e, preview=True)
    assert result is None
    mock_gw_cls.assert_not_called()


def test_maybe_gateway_non_preview_raises_when_anki_down(tmp_path: Path) -> None:
    """preview=False re-raises AnkiUnreachableError so the runner's exit-2 handler fires."""
    e2e = E2EConfig(test_home=tmp_path)
    with patch(_SOAK_GW) as mock_gw_cls:
        mock_instance = mock_gw_cls.return_value
        mock_instance.ping.side_effect = AnkiUnreachableError("connection refused")
        with pytest.raises(AnkiUnreachableError):
            _maybe_gateway(e2e, preview=False)


def test_maybe_gateway_non_preview_returns_gateway_when_anki_up(tmp_path: Path) -> None:
    """preview=False returns the gateway when ping succeeds."""
    e2e = E2EConfig(test_home=tmp_path)
    with patch(_SOAK_GW) as mock_gw_cls:
        mock_instance = mock_gw_cls.return_value
        mock_instance.ping.return_value = None
        result = _maybe_gateway(e2e, preview=False)
    assert result is mock_instance
    mock_instance.ping.assert_called_once()
    mock_instance.ensure_test_deck.assert_called_once()
    mock_instance.ensure_test_model.assert_called_once()

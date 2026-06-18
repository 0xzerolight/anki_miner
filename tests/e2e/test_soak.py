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

import pytest

from tests._home_isolation import restore_home_patches, set_test_home
from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.soak import (
    SessionReport,
    SoakReport,
    _child_cmd,
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


@pytest.fixture
def isolated_home(tmp_path: Path):
    """Point the process at a tmp home and restore the patches afterwards.

    The autouse conftest isolation already redirects the real home, but the soak
    runner is explicit about which home it uses, so we pin a per-test tmp home and
    restore the in-process binding patches on teardown.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    saved = set_test_home(home)
    try:
        yield home
    finally:
        restore_home_patches(saved)


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

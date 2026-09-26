from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from anki_miner.cli import entry
from anki_miner.cli.runner import ItemReport, SetupFailure
from anki_miner.config import paths as config_paths

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def out(capfd):
    """Parse everything written to fd 1 as JSON Lines."""

    def read() -> list[dict]:
        return [json.loads(line) for line in capfd.readouterr().out.splitlines()]

    return read


@pytest.fixture
def booted(monkeypatch, test_config):
    """Skip process boot and log sinks; pretend settings exist; use the test config."""
    monkeypatch.setattr(entry, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_start_log", lambda log_path: None)
    monkeypatch.setattr(entry, "_settings_exist", lambda: True)
    monkeypatch.setattr(entry.GUIConfigManager, "load_config", classmethod(lambda cls: test_config))
    return test_config


def _pair(tmp_path: Path, stem: str = "e1") -> list[str]:
    (tmp_path / f"{stem}.mkv").touch()
    (tmp_path / f"{stem}.srt").touch()
    return ["mine", "pairs", "--pair", str(tmp_path / f"{stem}.mkv"), str(tmp_path / f"{stem}.srt")]


class _FakeRun:
    reports: list[ItemReport] = []
    raise_: Exception | None = None
    seen_config = None

    def __init__(self, config, sink, cancel) -> None:
        type(self).seen_config = config
        self._cancel = cancel

    def run(self, jobs):
        if self.raise_ is not None:
            raise self.raise_
        return list(self.reports)


@pytest.fixture
def fake_run(monkeypatch):
    _FakeRun.reports = [ItemReport(item=0, kind="episode", input={}, status="success", cards_created=3)]
    _FakeRun.raise_ = None
    monkeypatch.setattr(entry, "MiningRun", _FakeRun)
    return _FakeRun


def test_version_prints_single_result(out) -> None:
    from anki_miner import __version__

    assert entry.main(["version"]) == 0
    assert out() == [
        {
            "event": "result",
            "schema": 1,
            "status": "success",
            "error": None,
            "cards_created": 0,
            "items": [],
            "app_version": __version__,
        }
    ]


def test_help_goes_to_stderr_and_still_ends_with_result(capfd) -> None:
    assert entry.main(["mine", "--help"]) == 0
    captured = capfd.readouterr()
    assert "usage" in captured.err.lower()
    [line] = captured.out.splitlines()
    assert json.loads(line)["status"] == "success"


def test_usage_error_is_json_exit_2(out) -> None:
    assert entry.main(["mine", "pairs"]) == 2
    [result] = out()
    assert result["event"] == "result" and result["status"] == "usage_error" and result["error"]


def test_missing_japanese_path_is_usage_error(out, tmp_path: Path) -> None:
    # Review focus 5.
    missing = tmp_path / "第01話.srt"
    (tmp_path / "第01話.mkv").touch()
    assert entry.main(["mine", "pairs", "--pair", str(tmp_path / "第01話.mkv"), str(missing)]) == 2
    [result] = out()
    assert result["status"] == "usage_error" and str(missing) in result["error"]


def test_success_emits_start_then_result(booted, fake_run, out, tmp_path: Path) -> None:
    assert entry.main(_pair(tmp_path)) == 0
    events = out()
    assert events[0]["event"] == "start"
    assert events[0]["schema"] == 1 and events[0]["command"] == "pairs" and events[0]["items"] == 1
    assert events[-1]["event"] == "result" and events[-1]["status"] == "success"
    assert events[-1]["cards_created"] == 3 and len(events[-1]["items"]) == 1


def test_deck_override_replaces_config(booted, fake_run, tmp_path: Path) -> None:
    entry.main([*_pair(tmp_path), "--deck", "Game::Chapter 1"])
    assert fake_run.seen_config.anki_deck_name == "Game::Chapter 1"


@pytest.mark.parametrize(("statuses", "status"), [(["success", "failed"], "partial"), (["failed"], "failed")])
def test_item_failures_exit_1(booted, fake_run, out, tmp_path: Path, statuses, status) -> None:
    fake_run.reports = [ItemReport(item=i, kind="episode", input={}, status=s) for i, s in enumerate(statuses)]
    assert entry.main(_pair(tmp_path)) == 1
    assert out()[-1]["status"] == status


def test_setup_failure_exit_4(booted, fake_run, out, tmp_path: Path) -> None:
    fake_run.raise_ = SetupFailure("Cannot connect to AnkiConnect")
    assert entry.main(_pair(tmp_path)) == 4
    result = out()[-1]
    assert result["status"] == "setup_error" and "AnkiConnect" in result["error"]
    assert result["items"] == [] and result["cards_created"] == 0


def test_unexpected_exception_is_error_json_not_traceback(booted, fake_run, out, tmp_path: Path) -> None:
    fake_run.raise_ = RuntimeError("kaboom")
    assert entry.main(_pair(tmp_path)) == 1
    result = out()[-1]
    assert result["status"] == "error" and "kaboom" in result["error"]


def test_not_set_up_is_setup_error(booted, fake_run, monkeypatch, out, tmp_path: Path) -> None:
    # Review focus 4: load_config would silently fall back to defaults.
    monkeypatch.setattr(entry, "_settings_exist", lambda: False)
    assert entry.main(_pair(tmp_path)) == 4
    result = out()[-1]
    assert result["status"] == "setup_error" and "no saved settings" in result["error"]


def test_settings_exist_follows_config_file_or_bak() -> None:
    # The real check, against the isolated home (conftest redirects CONFIG_FILE per test).
    config_file = entry.GUIConfigManager.CONFIG_FILE
    config_file.parent.mkdir(parents=True, exist_ok=True)
    assert not config_file.exists()
    assert not entry._settings_exist()
    bak = config_file.with_name(config_file.name + ".bak")
    bak.write_text("{}", encoding="utf-8")
    assert entry._settings_exist()
    bak.unlink()
    config_file.write_text("{}", encoding="utf-8")
    assert entry._settings_exist()


def test_fresh_home_is_not_busy(booted, fake_run, monkeypatch, out, tmp_path: Path) -> None:
    # Review focus 4: QLockFile cannot lock inside a missing directory.
    monkeypatch.setattr(config_paths, "ANKI_MINER_HOME", tmp_path / "fresh-home")
    assert entry.main(_pair(tmp_path)) == 0
    assert out()[-1]["status"] == "success"


def test_busy_when_lock_is_held(booted, fake_run, out, tmp_path: Path) -> None:
    from PyQt6.QtCore import QLockFile

    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    held = QLockFile(str(config_paths.ANKI_MINER_HOME / "instance.lock"))
    assert held.tryLock(0)
    try:
        assert entry.main(_pair(tmp_path)) == 3
    finally:
        held.unlock()
    [result] = out()
    assert result["status"] == "busy"


def test_cancelled_run_exits_130_and_releases_lock(booted, monkeypatch, out, tmp_path: Path) -> None:
    # SIGTERM mid-run → cancel event → 130, lock free afterwards. The installed
    # handler is invoked directly: a real kill would take down an xdist worker
    # if the handler were ever not installed.
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signal handlers can only be installed from the main thread")
    previous = signal.getsignal(signal.SIGTERM)

    class _Cancelling(_FakeRun):
        def run(self, jobs):
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler) and handler is not previous
            handler(signal.SIGTERM, None)
            assert self._cancel.is_set()
            return [ItemReport(item=0, kind="episode", input={}, status="cancelled")]

    monkeypatch.setattr(entry, "MiningRun", _Cancelling)
    assert entry.main(_pair(tmp_path)) == 130
    assert out()[-1]["status"] == "cancelled"
    assert signal.getsignal(signal.SIGTERM) == previous

    from PyQt6.QtCore import QLockFile

    lock = QLockFile(str(config_paths.ANKI_MINER_HOME / "instance.lock"))
    assert lock.tryLock(0)
    lock.unlock()


def test_stray_output_does_not_reach_stdout(booted, monkeypatch, capfd, tmp_path: Path) -> None:
    # Review focus 2: Python print, a raw fd-1 write (native library) and a child
    # process inheriting stdout (ffmpeg, yt-dlp) must all land on stderr.
    class _Noisy(_FakeRun):
        def run(self, jobs):
            print("python noise")
            os.write(1, b"native noise\n")
            subprocess.run([sys.executable, "-c", "print('child noise')"], check=True)
            # A cached reference to the real stdout, written without a flush
            # (block-buffered when stdout is a pipe, as under xdist).
            sys.__stdout__.write("buffered noise\n")
            return [ItemReport(item=0, kind="episode", input={}, status="success")]

    monkeypatch.setattr(entry, "MiningRun", _Noisy)
    assert entry.main(_pair(tmp_path)) == 0
    sys.__stdout__.flush()  # what interpreter exit would do, now that fd 1 is the caller's again
    captured = capfd.readouterr()
    assert [json.loads(line)["event"] for line in captured.out.splitlines()] == ["start", "result"]
    for noise in ("python noise", "native noise", "child noise", "buffered noise"):
        assert noise in captured.err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fd numbering: dup() takes the lowest free fd")
def test_closed_stderr_does_not_route_noise_into_the_event_stream() -> None:
    # A caller that closed fd 2 (``2>&-``, a daemon): dup(1) would land on fd 2,
    # and dup2(2, 1) would then point fd 1 back at the caller's stdout.
    code = (
        "import os, subprocess, sys\n"
        "os.close(2)\n"
        "from anki_miner.cli import entry\n"
        "with entry._private_stdout() as write:\n"
        "    print('python noise')\n"
        "    os.write(1, b'native noise\\n')\n"
        "    subprocess.run([sys.executable, '-c', 'print(\"child noise\")'], check=True)\n"
        "    write(b'EVENT\\n')\n"
        # Skip interpreter shutdown: flushing the closed stderr would exit 120.
        "os._exit(0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout == b"EVENT\n"


def test_commands_match_launch_dispatch_literal() -> None:
    from anki_miner.gui import launch

    assert launch.CLI_COMMANDS == entry.COMMANDS


def test_python_dash_m_version_subprocess(tmp_path: Path) -> None:
    # Real process: the dispatch in launch.main and the fd-1 write path. cwd =
    # repo root so the worktree's code (not the editable main checkout) runs;
    # an explicit isolated home so the child can never touch ~/.anki_miner.
    proc = subprocess.run(
        [sys.executable, "-m", "anki_miner", "version"],
        cwd=REPO_ROOT,
        env={**os.environ, "ANKI_MINER_HOME": str(tmp_path)},
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    [line] = proc.stdout.decode("ascii").splitlines()
    assert json.loads(line)["status"] == "success"


def _hold(path: Path):
    from PyQt6.QtCore import QLockFile

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    assert lock.tryLock(0)
    return lock


def test_busy_when_window_runs_past_the_warning(booted, fake_run, out, tmp_path: Path) -> None:
    # A window started past "already running" holds no instance.lock, only its marker.
    from anki_miner.gui.app import WINDOW_MARKER_PREFIX

    marker = _hold(config_paths.ANKI_MINER_HOME / f"{WINDOW_MARKER_PREFIX}{os.getpid()}.lock")
    try:
        assert entry.main(_pair(tmp_path)) == 3
    finally:
        marker.unlock()
    result = out()[-1]
    assert result["status"] == "busy" and "window is open" in result["error"]
    # instance.lock was released on the way out
    _hold(config_paths.ANKI_MINER_HOME / "instance.lock").unlock()


def test_busy_names_another_run(booted, fake_run, out, tmp_path: Path) -> None:
    # instance.lock held with no live window marker: every window holds one, so it is a run.
    held = _hold(config_paths.ANKI_MINER_HOME / "instance.lock")
    try:
        assert entry.main(_pair(tmp_path)) == 3
    finally:
        held.unlock()
    assert "command-line or API run" in out()[-1]["error"]


def test_hold_window_marker_is_seen_by_a_run(booted, fake_run, out, tmp_path: Path) -> None:
    from anki_miner.gui.app import _hold_window_marker

    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    assert marker is not None
    try:
        assert entry.main(_pair(tmp_path)) == 3
    finally:
        marker.unlock()
    assert "window is open" in out()[-1]["error"]


def test_stale_window_marker_is_cleared(booted, fake_run, out, tmp_path: Path) -> None:
    # A crashed window leaves its marker behind; its PID is gone, so it must not block.
    from anki_miner.gui.app import WINDOW_MARKER_PREFIX

    home = config_paths.ANKI_MINER_HOME
    home.mkdir(parents=True, exist_ok=True)
    marker = home / f"{WINDOW_MARKER_PREFIX}999999.lock"
    code = (
        "import os, sys\n"
        "from PyQt6.QtCore import QLockFile\n"
        "lock = QLockFile(sys.argv[1]); assert lock.tryLock(0)\n"
        "os._exit(0)\n"  # exit without unlocking: the file stays, its PID dies
    )
    subprocess.run([sys.executable, "-c", code, str(marker)], cwd=REPO_ROOT, check=True, timeout=60)
    assert marker.exists()
    assert entry.main(_pair(tmp_path)) == 0
    assert not marker.exists()

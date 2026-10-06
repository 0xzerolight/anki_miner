"""acquire_run_lock: which window or run refuses which call (API.md "Calling it")."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QLockFile

from anki_miner.cli import entry
from anki_miner.config import paths as config_paths

REPO_ROOT = Path(__file__).resolve().parents[2]


def _hold(path: Path) -> QLockFile:
    lock = QLockFile(str(path))
    assert lock.tryLock(0)
    return lock


def _free(path: Path) -> bool:
    probe = QLockFile(str(path))
    if probe.tryLock(0):
        probe.unlock()
        return True
    return False


@pytest.fixture
def home() -> Path:
    home = config_paths.ANKI_MINER_HOME
    home.mkdir(parents=True, exist_ok=True)
    return home


def test_beside_an_idle_window_only_the_run_lock_is_taken(home) -> None:
    window = _hold(home / f"instance.window-{os.getpid()}.lock")
    try:
        lock = entry.acquire_run_lock(beside_window=True)
        try:
            assert not _free(home / "instance.run.lock") and _free(home / "instance.lock")
        finally:
            lock.unlock()
    finally:
        window.unlock()
    assert _free(home / "instance.run.lock")


def test_a_mining_window_refuses_a_beside_window_run(home) -> None:
    window = _hold(home / f"instance.window-{os.getpid()}.lock")
    mining = _hold(home / f"instance.mining-{os.getpid()}.lock")
    try:
        with pytest.raises(entry.Busy, match="mining"):
            entry.acquire_run_lock(beside_window=True)
    finally:
        mining.unlock()
        window.unlock()
    assert _free(home / "instance.run.lock")


def test_any_open_window_refuses_the_other_runs(home) -> None:
    window = _hold(home / f"instance.window-{os.getpid()}.lock")
    try:
        with pytest.raises(entry.Busy, match="window is open"):
            entry.acquire_run_lock()
    finally:
        window.unlock()


@pytest.mark.parametrize("beside_window", [True, False])
def test_another_run_refuses(home, beside_window) -> None:
    other = _hold(home / "instance.run.lock")
    try:
        with pytest.raises(entry.Busy, match="command-line or API run"):
            entry.acquire_run_lock(beside_window=beside_window)
    finally:
        other.unlock()


def test_with_no_window_both_locks_are_held_then_released(home) -> None:
    lock = entry.acquire_run_lock(beside_window=True)
    try:
        assert not _free(home / "instance.lock") and not _free(home / "instance.run.lock")
    finally:
        lock.unlock()
    assert _free(home / "instance.lock") and _free(home / "instance.run.lock")


def test_a_held_instance_lock_gives_the_run_lock_back(home) -> None:
    held = _hold(home / "instance.lock")
    try:
        with pytest.raises(entry.Busy, match="command-line or API run"):
            entry.acquire_run_lock()
    finally:
        held.unlock()
    assert _free(home / "instance.run.lock")


def test_a_crashed_windows_mining_marker_is_cleared(home) -> None:
    marker = home / "instance.mining-999999.lock"
    code = (
        "import os, sys\n"
        "from PyQt6.QtCore import QLockFile\n"
        "lock = QLockFile(sys.argv[1]); assert lock.tryLock(0)\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", code, str(marker)], cwd=REPO_ROOT, check=True, timeout=60)
    assert marker.exists()
    entry.acquire_run_lock(beside_window=True).unlock()
    assert not marker.exists()


def test_a_window_skips_store_repair_while_a_run_holds_the_run_lock(home, test_config, monkeypatch) -> None:
    from anki_miner.gui import app

    repaired = []
    monkeypatch.setattr(app, "run_startup_store_recovery", lambda config, **kw: repaired.append(config))
    own = _hold(home / "instance.lock")  # the booting window's own instance lock
    try:
        run = _hold(home / "instance.run.lock")
        try:
            app._run_store_recovery_if_locked(test_config, own, allow_collection=True)
        finally:
            run.unlock()
        assert repaired == []
        app._run_store_recovery_if_locked(test_config, own, allow_collection=True)
        assert repaired == [test_config]
    finally:
        own.unlock()


def test_a_beside_window_run_is_refused_while_the_window_repairs_its_stores(home, test_config, monkeypatch) -> None:
    from anki_miner.gui import app

    refused = []

    def repair(config, **kw) -> None:
        # A --api mine starting mid-repair sees the window's marker and would take only the run lock.
        try:
            entry.acquire_run_lock(beside_window=True).unlock()
        except entry.Busy:
            refused.append(True)
        else:
            refused.append(False)

    monkeypatch.setattr(app, "run_startup_store_recovery", repair)
    window = _hold(home / f"instance.window-{os.getpid()}.lock")
    own = _hold(home / "instance.lock")
    try:
        app._run_store_recovery_if_locked(test_config, own, allow_collection=True)
    finally:
        own.unlock()
        window.unlock()
    assert refused == [True]
    assert _free(home / "instance.run.lock")

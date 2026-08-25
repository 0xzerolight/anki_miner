"""Tests for bounded subprocess supervision."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import psutil
import pytest

from anki_miner.utils.process_supervisor import _RETAIN_TAIL_LINES, SupervisedState, run_supervised


def _pid_is_live(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _pid_dies_within(pid: int, timeout_s: float = 2.0) -> bool:
    """Poll until ``pid`` is dead, bounded by ``timeout_s``.

    killpg queues the signal and returns; the process dies asynchronously.
    A single instant check right after run_supervised returns races the
    scheduler and flakes under load — death within a short bound is the
    actual contract.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_is_live(pid):
            return True
        time.sleep(0.01)
    return not _pid_is_live(pid)


class _FakePipe:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks: queue.Queue[bytes] = queue.Queue()
        for chunk in chunks:
            self._chunks.put(chunk)

    def read(self, _size: int) -> bytes:
        return self._chunks.get(timeout=1)

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, stdout: list[bytes], returncode: int | None) -> None:
        self.pid = 4242
        self.stdout = _FakePipe(stdout)
        self.stderr = None
        self.returncode = returncode
        self.wait_timeouts: list[float | None] = []
        self._done = threading.Event()
        if returncode is not None:
            self._done.set()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        self._done.set()
        self.stdout._chunks.put(b"")


def test_supervised_child_killed_on_deadline() -> None:
    proc = _FakeProcess([], None)

    def killpg(_pid: int, _sig: int) -> None:
        proc.finish(-signal.SIGTERM)

    with (
        patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc),
        patch("anki_miner.utils.process_supervisor.os.killpg", side_effect=killpg) as kill,
    ):
        result = run_supervised(["fake"], timeout_s=0.01, combine_stderr=True)

    assert result.state is SupervisedState.TIMED_OUT
    assert kill.called


def test_supervised_popen_detaches_stdin() -> None:
    proc = _FakeProcess([b"line\n", b""], 0)
    with patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc) as popen:
        run_supervised(["fake"], timeout_s=0.1, combine_stderr=True)

    assert popen.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_supervised_tree_killed_and_reaped() -> None:
    proc = _FakeProcess([], None)
    signals: list[int] = []

    def killpg(_pid: int, sig: int) -> None:
        signals.append(sig)
        if sig == signal.SIGKILL:
            proc.finish(-sig)

    with (
        patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc) as popen,
        patch("anki_miner.utils.process_supervisor.os.killpg", side_effect=killpg),
        patch("anki_miner.utils.process_supervisor._TERMINATE_GRACE_S", 0.01),
    ):
        result = run_supervised(["fake"], timeout_s=0.01, combine_stderr=True)

    assert result.state is SupervisedState.TIMED_OUT
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert popen.call_args.kwargs["start_new_session"] is True
    assert proc.wait_timeouts
    assert all(timeout is not None for timeout in proc.wait_timeouts)


def test_supervised_nonutf8_output_does_not_wedge() -> None:
    proc = _FakeProcess([b"before\xffafter", b""], 0)
    with patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc):
        result = run_supervised(["fake"], timeout_s=0.1, combine_stderr=True)

    assert result.state is SupervisedState.COMPLETED
    assert result.stdout == "before\ufffdafter"


def test_supervised_raising_callback_contained(caplog: Any) -> None:
    proc = _FakeProcess([b"first\nsecond\n", b""], 0)
    seen: list[str] = []

    def raising_callback(line: str) -> None:
        seen.append(line)
        raise RuntimeError("callback broke")

    with patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc):
        result = run_supervised(
            ["fake"],
            timeout_s=0.1,
            combine_stderr=True,
            line_callback=raising_callback,
        )

    assert result.state is SupervisedState.COMPLETED
    assert seen == ["first", "second"]
    assert "supervised process callback failed" in caplog.text


def test_supervised_retain_output_false_bounds_tail() -> None:
    line_count = 10_000
    chunks = [f"{i}\n".encode() for i in range(line_count)]
    proc = _FakeProcess([*chunks, b""], 0)
    seen: list[str] = []
    with patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc):
        result = run_supervised(
            ["fake"],
            timeout_s=5.0,
            combine_stderr=True,
            line_callback=seen.append,
            retain_output=False,
        )

    assert result.state is SupervisedState.COMPLETED
    # the callback still sees every line — only stored retention is bounded.
    assert seen == [str(i) for i in range(line_count)]
    retained_lines = result.stdout.splitlines()
    assert len(retained_lines) <= _RETAIN_TAIL_LINES
    assert retained_lines[-1] == str(line_count - 1)
    assert "0" not in retained_lines


def test_supervised_retain_output_default_keeps_everything() -> None:
    line_count = 10_000
    chunks = [f"{i}\n".encode() for i in range(line_count)]
    proc = _FakeProcess([*chunks, b""], 0)
    with patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc):
        result = run_supervised(["fake"], timeout_s=5.0, combine_stderr=True)

    assert result.state is SupervisedState.COMPLETED
    assert result.stdout.splitlines() == [str(i) for i in range(line_count)]


def test_supervised_windows_job_terminates_tree() -> None:
    proc = _FakeProcess([], None)
    job = MagicMock()
    job.terminate.side_effect = lambda: proc.finish(1)

    with (
        patch("anki_miner.utils.process_supervisor.sys.platform", "win32"),
        patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc) as popen,
        patch("anki_miner.utils.process_supervisor._WindowsJob.create", return_value=job) as create_job,
        patch("anki_miner.utils.process_supervisor._windows_descendant_pids", return_value=set()),
    ):
        result = run_supervised(["fake"], timeout_s=0.01, combine_stderr=True)

    assert result.state is SupervisedState.TIMED_OUT
    create_job.assert_called_once_with(proc)
    job.terminate.assert_called_once()
    job.close.assert_called_once()
    assert popen.call_args.kwargs["creationflags"] & 0x08000000
    assert popen.call_args.kwargs["creationflags"] & 0x00000200


def test_supervised_windows_job_failure_rescans_descendants() -> None:
    proc = _FakeProcess([], None)
    proc.terminate = MagicMock(side_effect=lambda: proc.finish(1))  # type: ignore[attr-defined]
    proc.kill = MagicMock()  # type: ignore[attr-defined]
    job = MagicMock()
    job.terminate.return_value = False

    with (
        patch("anki_miner.utils.process_supervisor.sys.platform", "win32"),
        patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc),
        patch("anki_miner.utils.process_supervisor._WindowsJob.create", return_value=job),
        patch(
            "anki_miner.utils.process_supervisor._windows_descendant_pids",
            side_effect=[{4300}, {4300, 4301}, {4300, 4301}],
        ) as descendants,
        patch("anki_miner.utils.process_supervisor._terminate_windows_pid") as terminate_pid,
        patch("anki_miner.utils.process_supervisor._TERMINATE_GRACE_S", 0.01),
    ):
        result = run_supervised(["fake"], timeout_s=0.01, combine_stderr=True)

    assert result.state is SupervisedState.TIMED_OUT
    assert descendants.call_count >= 2
    terminate_pid.assert_any_call(4300, ANY)
    terminate_pid.assert_any_call(4301, ANY)


def test_supervised_failed_parent_terminates_remaining_tree() -> None:
    proc = _FakeProcess([b"failure\n", b""], 1)
    with (
        patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc),
        patch("anki_miner.utils.process_supervisor.os.killpg") as killpg,
    ):
        result = run_supervised(["fake"], timeout_s=0.1, combine_stderr=True)

    assert result.state is SupervisedState.FAILED
    killpg.assert_any_call(proc.pid, signal.SIGTERM)
    killpg.assert_any_call(proc.pid, signal.SIGKILL)


def test_supervised_late_cancel_still_terminates_tree() -> None:
    cancel = threading.Event()
    proc = _FakeProcess([b"done\n", b""], 0)
    original_wait = proc.wait

    def wait_and_cancel(timeout: float | None = None) -> int:
        cancel.set()
        return original_wait(timeout)

    proc.wait = wait_and_cancel  # type: ignore[method-assign]
    with (
        patch("anki_miner.utils.process_supervisor.subprocess.Popen", return_value=proc),
        patch("anki_miner.utils.process_supervisor.os.killpg") as killpg,
    ):
        result = run_supervised(["fake"], timeout_s=0.1, cancel=cancel, combine_stderr=True)

    assert result.state is SupervisedState.CANCELLED
    killpg.assert_any_call(proc.pid, signal.SIGTERM)
    killpg.assert_any_call(proc.pid, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group integration coverage")
def test_supervised_success_reaps_descendant_within_total_bound(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    code = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
        "time.sleep(0.05)"
    )
    started = time.monotonic()
    result = run_supervised([sys.executable, "-c", code, str(child_pid_path)], timeout_s=1.0)
    elapsed = time.monotonic() - started
    child_pid = int(child_pid_path.read_text())
    try:
        assert result.state is SupervisedState.COMPLETED
        assert elapsed < 2.0
        assert _pid_dies_within(child_pid)
    finally:
        if _pid_is_live(child_pid):
            os.kill(child_pid, signal.SIGKILL)

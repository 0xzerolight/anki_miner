"""Bounded subprocess execution with process-tree containment."""

from __future__ import annotations

import codecs
import contextlib
import ctypes
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.02
_TERMINATE_GRACE_S = 0.25
_DRAIN_TIMEOUT_S = 0.5
_REAP_TIMEOUT_S = 0.5
_READ_SIZE = 64 * 1024
_WINDOWS_RESCAN_INTERVAL_S = 0.02
# Bound for parts[name] when retain_output=False: a multi-hour streaming
# transfer (yt-dlp HLS fragments) emits a callback line per fragment, so
# retaining every decoded chunk forever grows unboundedly. Callers that
# already keep their own tail (youtube_fetcher fetch, media_downloader
# download) don't read SupervisedResult.stdout/stderr, so this only needs to
# cover the crash-diagnostics case, not full output fidelity.
_RETAIN_TAIL_LINES = 200


class SupervisedState(Enum):
    COMPLETED = "completed"
    TIMED_OUT = "timed-out"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class SupervisedResult:
    state: SupervisedState
    returncode: int | None
    stdout: str
    stderr: str
    error: BaseException | None = None


class _WindowsJob:
    """Minimal Win32 Job Object wrapper; children join their parent's job."""

    def __init__(self, kernel32: object, handle: int) -> None:
        self._kernel32 = kernel32
        self._handle = handle

    @classmethod
    def create(cls, proc: Any) -> _WindowsJob | None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            kernel32.CreateJobObjectW.restype = ctypes.c_void_p
            kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
            kernel32.AssignProcessToJobObject.restype = ctypes.c_int
            kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            kernel32.TerminateJobObject.restype = ctypes.c_int
            kernel32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(getattr(ctypes, "get_last_error", lambda: 0)(), "CreateJobObjectW failed")
            raw_process_handle = proc._handle
            process_handle = ctypes.c_void_p(int(raw_process_handle))
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                error = OSError(getattr(ctypes, "get_last_error", lambda: 0)(), "AssignProcessToJobObject failed")
                kernel32.CloseHandle(handle)
                raise error
            return cls(kernel32, int(handle))
        except (AttributeError, OSError, TypeError, ValueError):
            logger.warning("could not enroll supervised process in a Windows Job Object", exc_info=True)
            return None

    def terminate(self) -> bool:
        if self._handle:
            return bool(
                self._kernel32.TerminateJobObject(ctypes.c_void_p(self._handle), 1)  # type: ignore[attr-defined]
            )
        return True

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(ctypes.c_void_p(self._handle))  # type: ignore[attr-defined]
            self._handle = 0


def _reader(
    name: str,
    stream: object,
    output: queue.Queue[tuple[str, bytes | None, BaseException | None]],
) -> None:
    try:
        while True:
            chunk = stream.read(_READ_SIZE)  # type: ignore[attr-defined]
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise TypeError(f"{name} pipe returned non-bytes data")
            output.put((name, chunk, None))
    except BaseException as exc:  # noqa: BLE001 - reader failure is returned, never escapes the daemon thread
        output.put((name, None, exc))
    finally:
        output.put((name, None, None))


def _cancel_requested(cancel: threading.Event | Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    if isinstance(cancel, threading.Event):
        return cancel.is_set()
    return bool(cancel())


def _windows_descendant_pids(ancestor_pids: set[int]) -> set[int]:
    class ProcessEntry(ctypes.Structure):
        _fields_ = (
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = (ctypes.c_ulong, ctypes.c_ulong)
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ProcessEntry))
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ProcessEntry))
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError(getattr(ctypes, "get_last_error", lambda: 0)(), "process snapshot failed")
    children: dict[int, set[int]] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            children.setdefault(int(entry.th32ParentProcessID), set()).add(int(entry.th32ProcessID))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    descendants: set[int] = set()
    pending = list(ancestor_pids)
    while pending:
        for pid in children.get(pending.pop(), set()):
            if pid not in ancestor_pids and pid not in descendants:
                descendants.add(pid)
                pending.append(pid)
    return descendants


def _terminate_windows_pid(pid: int, deadline: float) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(0x00100001, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        kernel32.WaitForSingleObject(handle, min(remaining_ms, 20))
    finally:
        kernel32.CloseHandle(handle)


def _terminate_windows_tree(proc: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
    known_pids = {proc.pid}
    rescan_failed = False
    deadline = time.monotonic() + _TERMINATE_GRACE_S

    def rescan() -> None:
        nonlocal rescan_failed
        try:
            known_pids.update(_windows_descendant_pids(known_pids))
        except (AttributeError, OSError, TypeError, ValueError):
            if not rescan_failed:
                logger.warning("could not rescan supervised Windows descendants", exc_info=True)
                rescan_failed = True

    rescan()
    if job is not None:
        try:
            if not job.terminate():
                logger.warning("could not terminate supervised Windows Job Object")
        except (AttributeError, OSError, TypeError, ValueError):
            logger.warning("could not terminate supervised Windows Job Object", exc_info=True)
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.terminate()
    while True:
        for pid in known_pids - {proc.pid}:
            with contextlib.suppress(OSError):
                _terminate_windows_pid(pid, deadline)
        if time.monotonic() >= deadline:
            break
        time.sleep(min(_WINDOWS_RESCAN_INTERVAL_S, max(0.0, deadline - time.monotonic())))
        rescan()
    rescan()
    for pid in known_pids - {proc.pid}:
        with contextlib.suppress(OSError):
            _terminate_windows_pid(pid, deadline)
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.kill()


def _terminate_tree(proc: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
    if sys.platform == "win32":
        _terminate_windows_tree(proc, job)
        return

    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + _TERMINATE_GRACE_S
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_S)
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)


def _bounded_wait(proc: subprocess.Popen[bytes]) -> int | None:
    try:
        return proc.wait(timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return proc.poll()


def run_supervised(
    command: Sequence[str | os.PathLike[str]],
    *,
    timeout_s: float,
    cancel: threading.Event | Callable[[], bool] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    line_callback: Callable[[str], None] | None = None,
    combine_stderr: bool = False,
    encoding: str = "utf-8",
    retain_output: bool = True,
) -> SupervisedResult:
    """Run *command* to one terminal state without unbounded pipe reads or waits."""
    started = time.monotonic()
    deadline = started + max(timeout_s, 0.0)
    popen_kwargs: dict[str, Any] = {
        # Detach stdin: a backgrounded child reading the controlling terminal gets
        # SIGTTIN-stopped (see media_extractor.py's _run_ffmpeg for the full story).
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT if combine_stderr else subprocess.PIPE,
        "bufsize": 0,
        "env": env,
        "cwd": Path(cwd) if cwd is not None else None,
        "text": False,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc: subprocess.Popen[bytes] = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        return SupervisedResult(SupervisedState.FAILED, None, "", "", exc)

    job = _WindowsJob.create(proc) if sys.platform == "win32" else None
    events: queue.Queue[tuple[str, bytes | None, BaseException | None]] = queue.Queue()
    streams = {"stdout": proc.stdout, "stderr": proc.stderr}
    readers: list[threading.Thread] = []
    ended = {name for name, stream in streams.items() if stream is None}
    for name, stream in streams.items():
        if stream is None:
            continue
        thread = threading.Thread(
            target=_reader,
            args=(name, stream, events),
            daemon=True,
            name=f"process-supervisor-{name}",
        )
        readers.append(thread)
        thread.start()

    decoders = {
        "stdout": codecs.getincrementaldecoder(encoding)(errors="replace"),
        "stderr": codecs.getincrementaldecoder(encoding)(errors="replace"),
    }
    parts: dict[str, list[str] | deque[str]] = (
        {"stdout": [], "stderr": []}
        if retain_output
        else {"stdout": deque(maxlen=_RETAIN_TAIL_LINES), "stderr": deque(maxlen=_RETAIN_TAIL_LINES)}
    )
    pending: dict[str, str] = {"stdout": "", "stderr": ""}
    reader_error: BaseException | None = None

    def emit_line(line: str) -> None:
        if line_callback is None:
            return
        try:
            line_callback(line)
        except BaseException:  # noqa: BLE001 - callbacks cannot break supervision
            logger.exception("supervised process callback failed")

    def consume(name: str, chunk: bytes | None, error: BaseException | None) -> None:
        nonlocal reader_error
        if error is not None:
            reader_error = reader_error or error
            return
        if chunk is None:
            if name in ended:
                return
            text = decoders[name].decode(b"", final=True)
            if text:
                parts[name].append(text)
                pending[name] += text
            if pending[name]:
                emit_line(pending[name].removesuffix("\r"))
                pending[name] = ""
            ended.add(name)
            return
        text = decoders[name].decode(chunk)
        parts[name].append(text)
        pending[name] += text
        while "\n" in pending[name]:
            line, pending[name] = pending[name].split("\n", 1)
            emit_line(line.removesuffix("\r"))

    state: SupervisedState | None = None
    terminal_error: BaseException | None = None
    while state is None:
        try:
            if _cancel_requested(cancel):
                state = SupervisedState.CANCELLED
            elif time.monotonic() >= deadline:
                state = SupervisedState.TIMED_OUT
            else:
                returncode = proc.poll()
                if returncode is not None:
                    state = SupervisedState.COMPLETED if returncode == 0 else SupervisedState.FAILED
        except BaseException as exc:  # noqa: BLE001 - a broken predicate becomes one failed terminal result
            terminal_error = exc
            state = SupervisedState.FAILED
        if state is not None:
            break
        wait_s = min(_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic()))
        with contextlib.suppress(queue.Empty):
            consume(*events.get(timeout=wait_s))

    # A successful parent can still leave a helper holding inherited pipes or
    # running in its process group. Supervision owns the whole tree, not only the
    # immediate process, so every terminal state closes descendants.
    _terminate_tree(proc, job)
    returncode = _bounded_wait(proc)

    drain_deadline = time.monotonic() + _DRAIN_TIMEOUT_S
    while len(ended) < len(streams) and time.monotonic() < drain_deadline:
        try:
            consume(*events.get(timeout=min(_POLL_INTERVAL_S, drain_deadline - time.monotonic())))
        except queue.Empty:
            if all(not reader.is_alive() for reader in readers):
                break
    while True:
        try:
            consume(*events.get_nowait())
        except queue.Empty:
            break

    terminate_after_drain = False
    if state in {SupervisedState.COMPLETED, SupervisedState.FAILED}:
        try:
            if _cancel_requested(cancel):
                state = SupervisedState.CANCELLED
                terminate_after_drain = True
        except BaseException as exc:  # noqa: BLE001 - a broken predicate is one failed result
            terminal_error = exc
            state = SupervisedState.FAILED
            terminate_after_drain = True
    if reader_error is not None and state is SupervisedState.COMPLETED:
        state = SupervisedState.FAILED
        terminal_error = reader_error
        terminate_after_drain = True
    elif terminal_error is None and state is SupervisedState.FAILED:
        terminal_error = reader_error
    if terminate_after_drain:
        _terminate_tree(proc, job)
    if job is not None:
        try:
            job.close()
        except (AttributeError, OSError, TypeError, ValueError):
            logger.warning("could not close supervised Windows Job Object", exc_info=True)
    return SupervisedResult(
        state,
        returncode,
        "".join(parts["stdout"]),
        "".join(parts["stderr"]),
        terminal_error,
    )

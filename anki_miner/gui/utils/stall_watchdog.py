"""Runtime detector for main (GUI) thread stalls.

Part of the GUI-freeze-hardening effort. The watchdog turns silent "Not
responding" freezes into logged WARNINGs carrying the GUI thread's stack
trace plus a counter, so future regressions surface in logs (and the e2e
report) instead of going unnoticed. It is on by default in the shipped app
and has near-zero overhead: a 250ms heartbeat QTimer on the GUI thread and a
daemon monitor thread that polls a shared timestamp.

How it works:

* The **heartbeat** :class:`~PyQt6.QtCore.QTimer` fires on the GUI thread and
  records ``time.monotonic()``. While the GUI thread spins its event loop the
  timestamp stays fresh; the moment the thread blocks (long synchronous work,
  a stuck call) the timestamp goes stale.
* The **monitor** :class:`threading.Thread` (daemon) wakes every ``poll_ms``
  and computes ``now - last_tick``. When that gap exceeds ``threshold_ms`` it
  records one stall episode: bumps the per-instance and module-global
  counters, stores the observed gap, and logs a WARNING with the GUI thread's
  current stack. It reports at most once per episode and re-arms only after a
  fresh heartbeat tick proves the GUI thread recovered.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from typing import Any

from PyQt6.QtCore import QObject, QTimer

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

# A paused span longer than this is worth an INFO receipt rather than a DEBUG
# one: past a second the user saw the app freeze, and the log has to say the
# freeze was the deliberate block (a theme repolish) and not a real stall. A
# module constant, not a setting — nothing about it is worth a user decision.
_PAUSE_INFO_MS = 1000

# Module-global stall counter so the e2e harness (and tests) can read totals
# without holding a handle to whichever StallWatchdog instance is live.
_global_stall_count = 0
_global_lock = threading.Lock()

# Module-global pause depth. Call sites that perform a deliberately-synchronous
# GUI-thread block which CANNOT be moved off-thread (e.g. an unavoidable Qt
# stylesheet repolish on a theme/font-scale change) wrap it in
# ``paused_stall_detection()`` so the resulting heartbeat gap is not reported as
# a stall. A depth counter (not a bool) keeps nested pauses correct. Every live
# StallWatchdog consults this so call sites need no handle to the watchdog. The
# lock guards the depth across the GUI thread (which mutates it) and the monitor
# thread (which reads it).
_pause_depth = 0
_pause_lock = threading.Lock()

# Watchdogs register themselves here while started so a pause exit can refresh
# every live instance's heartbeat (so the paused span is not counted as a stall
# after resume). Guarded by its own lock; entries are added in start() and
# removed in stop().
_live_watchdogs: set[StallWatchdog] = set()
_watchdogs_lock = threading.Lock()


def _stall_detection_paused() -> bool:
    """Return True while inside one or more ``paused_stall_detection()`` blocks."""
    with _pause_lock:
        return _pause_depth > 0


@contextlib.contextmanager
def paused_stall_detection(label: str = "") -> Iterator[None]:
    """Suppress stall reporting around a deliberately-synchronous GUI-thread block.

    Use ONLY for work that genuinely cannot be moved off the GUI thread — the
    canonical case is the unavoidable Qt stylesheet repolish triggered by a
    theme or font-scale change (a multi-second freeze the app documents and
    cannot avoid). Keep the wrapped span as tight as possible so real stalls
    elsewhere are still detected.

    While the block is active, every live :class:`StallWatchdog` skips recording
    and logging stalls. On exit, every live watchdog's heartbeat timestamp is
    refreshed to "now" so the just-elapsed paused span is not itself reported as
    a stall on the next monitor poll.

    The exit also logs how long the block actually ran. Without it a suppressed
    span is invisible, and "the app hangs when I change theme" cannot be told
    apart from a real stall the watchdog never got to see: the pause is exactly
    the window in which stall reporting is off.

    Args:
        label: What is being blocked on, e.g. ``"theme repolish"``. Appears in
            the resume line; pass one at every call site.
    """
    global _pause_depth
    with _pause_lock:
        _pause_depth += 1
    started_at = time.monotonic()
    try:
        yield
    finally:
        # Refresh every live watchdog's heartbeat BEFORE clearing the pause, so
        # the monitor thread never observes the (stale) paused span as a stall
        # in the window between decrementing the depth and refreshing.
        with _watchdogs_lock:
            watchdogs = list(_live_watchdogs)
        for wd in watchdogs:
            wd._refresh_heartbeat()
        with _pause_lock:
            _pause_depth -= 1
        elapsed_ms = (time.monotonic() - started_at) * 1000
        level = logging.INFO if elapsed_ms > _PAUSE_INFO_MS else logging.DEBUG
        logger.log(
            level,
            "stall detection resumed: %s after %.0f ms",
            label or "(unlabelled)",
            elapsed_ms,
        )


def get_global_stall_count() -> int:
    """Return the total stall episodes observed across all watchdogs."""
    with _global_lock:
        return _global_stall_count


def reset_global_stall_count() -> None:
    """Reset the module-global stall counter to zero."""
    global _global_stall_count
    with _global_lock:
        _global_stall_count = 0


def _bump_global() -> None:
    global _global_stall_count
    with _global_lock:
        _global_stall_count += 1


def _dump_sink() -> Any:
    """Return the file the shutdown dump writes to.

    The crash file, not stderr, for the reason ``_format_main_stack`` gives: a
    frozen build has no stderr anyone can read. Imported lazily — a module-level
    import of ``gui.app`` would be a cycle.
    """
    from anki_miner.gui.app import crash_stream

    return crash_stream() or sys.stderr


def dump_stacks_later(seconds: float) -> bool:
    """Arm a one-shot all-thread stack dump ``seconds`` from now.

    Armed just before a shutdown path that is expected to finish quickly. If it
    finishes, :func:`cancel_stack_dump` disarms the timer and nothing is
    written; if the process wedges instead, the dump lands in the crash file and
    names the thread that is stuck — the only evidence available for a hang
    after the window closed, where no Python code of ours ever runs again.

    Args:
        seconds: Delay before the dump fires.

    Returns:
        True if the timer was armed, False if faulthandler refused the sink
        (a stream with no usable file descriptor).
    """
    sink = _dump_sink()
    try:
        faulthandler.dump_traceback_later(seconds, exit=False, file=sink)
    except Exception as exc:  # noqa: BLE001 — bucket: faulthandler refused the sink
        log_summary(
            logger,
            "stall watchdog stack dump refused",
            seconds=seconds,
            error_type=type(exc).__name__,
            error=str(exc),
            level=logging.DEBUG,
        )
        return False
    log_summary(logger, "stall watchdog stack dump armed", seconds=seconds, level=logging.DEBUG)
    return True


def cancel_stack_dump() -> None:
    """Disarm a timer armed by :func:`dump_stacks_later`. Safe if none is armed."""
    faulthandler.cancel_dump_traceback_later()


class StallWatchdog:
    """Detect and log stalls of the GUI (main) thread.

    Constructed on the GUI thread (it captures that thread's id for stack
    introspection). Call :meth:`start` once the event loop is running and
    :meth:`stop` during shutdown.
    """

    def __init__(
        self,
        *,
        threshold_ms: int = 1000,
        poll_ms: int = 250,
        parent: QObject | None = None,
    ) -> None:
        """Initialize the watchdog.

        Args:
            threshold_ms: Heartbeat gap (ms) above which a stall is reported.
            poll_ms: Heartbeat QTimer interval and monitor poll cadence (ms).
            parent: Optional QObject parent for the heartbeat QTimer.
        """
        self._threshold_ms = threshold_ms
        self._poll_ms = poll_ms
        self._parent = parent

        # Captured on the GUI thread at construction time.
        self._main_thread_id = threading.get_ident()

        # Shared heartbeat timestamp, guarded for clarity (float writes are
        # atomic in CPython, but the lock documents the cross-thread handoff).
        self._tick_lock = threading.Lock()
        self._last_tick = time.monotonic()

        self._timer: QTimer | None = None
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Whether the current stall episode has already been reported. Reset
        # once a fresh heartbeat tick proves the GUI thread recovered.
        self._reported_current_stall = False

        self._stall_count = 0
        self._last_stall_ms: float | None = None

        # Whether a start() has happened that no stop() has answered yet. Gates
        # the shutdown receipt so a stop-before-start or a second stop does not
        # log a second "stopped" line for a watchdog that was never running.
        self._started = False

    # --- Public API --------------------------------------------------------

    @property
    def stall_count(self) -> int:
        """Number of stall episodes this instance has observed."""
        return self._stall_count

    @property
    def last_stall_ms(self) -> float | None:
        """Observed gap (ms) of the most recent stall, or None if none yet."""
        return self._last_stall_ms

    def start(self) -> None:
        """Start the heartbeat timer and monitor thread. Idempotent."""
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()
        with self._tick_lock:
            self._last_tick = time.monotonic()
        self._reported_current_stall = False

        self._timer = QTimer(self._parent)
        self._timer.setInterval(self._poll_ms)
        self._timer.timeout.connect(self._on_heartbeat)
        self._timer.start()

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="StallWatchdog",
            daemon=True,
        )
        self._monitor_thread.start()

        with _watchdogs_lock:
            _live_watchdogs.add(self)
        self._started = True

    def stop(self) -> None:
        """Stop the heartbeat timer, signal the monitor to exit, and join it.

        Safe to call if never started or more than once. A watchdog that ran
        logs its episode count on the way down: a session that froze four times
        and one that never froze otherwise look identical in the log.
        """
        self._stop_event.set()

        with _watchdogs_lock:
            _live_watchdogs.discard(self)

        if self._timer is not None:
            self._timer.stop()
            self._timer.timeout.disconnect(self._on_heartbeat)
            self._timer = None

        monitor = self._monitor_thread
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=max(1.0, self._poll_ms / 1000 * 4))
        self._monitor_thread = None

        if self._started:
            self._started = False
            log_summary(
                logger,
                "stall watchdog stopped",
                stalls=self._stall_count,
                total=get_global_stall_count(),
                last_stall_ms=None if self._last_stall_ms is None else round(self._last_stall_ms),
            )

    # --- Internals ---------------------------------------------------------

    def _on_heartbeat(self) -> None:
        """GUI-thread slot: stamp the shared heartbeat timestamp."""
        with self._tick_lock:
            self._last_tick = time.monotonic()
        # A fresh tick means the GUI thread is alive again; re-arm reporting.
        self._reported_current_stall = False

    def _refresh_heartbeat(self) -> None:
        """Stamp the heartbeat to ``now`` and re-arm reporting.

        Called by :func:`paused_stall_detection` on exit (from the GUI thread)
        so the paused span — during which the GUI thread legitimately stopped
        ticking — is not counted as a stall on the next monitor poll.
        """
        with self._tick_lock:
            self._last_tick = time.monotonic()
        self._reported_current_stall = False

    def _monitor_loop(self) -> None:
        """Monitor-thread loop: detect heartbeat gaps over the threshold."""
        threshold_s = self._threshold_ms / 1000
        poll_s = self._poll_ms / 1000
        while not self._stop_event.wait(poll_s):
            # Skip while a deliberately-synchronous block is in progress; the
            # gap it produces is expected and must not be reported.
            if _stall_detection_paused():
                continue
            with self._tick_lock:
                last_tick = self._last_tick
            gap_s = time.monotonic() - last_tick
            if gap_s > threshold_s and not self._reported_current_stall:
                self._record_stall(gap_s)

    def _record_stall(self, gap_s: float) -> None:
        """Record and log one stall episode (called from the monitor thread)."""
        self._reported_current_stall = True
        gap_ms = gap_s * 1000
        self._stall_count += 1
        self._last_stall_ms = gap_ms
        _bump_global()

        stack_text = self._format_main_stack()
        # episode/total separate the one-off freeze from the repeat offender:
        # a single 1.2 s stall is noise, the twentieth in a session is the bug.
        logger.warning(
            "GUI thread stall detected: %.0f ms (threshold %d ms) episode=%d total=%d." " Main-thread stack:\n%s",
            gap_ms,
            self._threshold_ms,
            self._stall_count,
            get_global_stall_count(),
            stack_text,
        )

    def _format_main_stack(self) -> str:
        """Return the GUI thread's current stack as text, best-effort.

        Falls back to dumping all tracebacks to the crash file when the frame
        for the main thread cannot be resolved.
        """
        frame = sys._current_frames().get(self._main_thread_id)
        if frame is not None:
            return "".join(traceback.format_stack(frame))
        # Frame unavailable — dump everything as a backstop. To the crash file
        # rather than stderr: a frozen build has no stderr to read, so that
        # backstop used to discard the only evidence it had.
        from anki_miner.gui.app import crash_stream

        sink = crash_stream() or sys.stderr
        with contextlib.suppress(Exception):
            faulthandler.dump_traceback(file=sink)
        return "(main-thread frame unavailable; traceback dumped to the crash log)"


def install_stall_watchdog(window: QObject) -> StallWatchdog:
    """Construct, store, and start a :class:`StallWatchdog` for ``window``.

    The instance is stored on ``window._stall_watchdog`` so it is not garbage
    collected, then started. The returned watchdog's :meth:`~StallWatchdog.stop`
    is safe to call even if start somehow failed.

    Args:
        window: The main window (used as QTimer parent and instance holder).

    Returns:
        The started :class:`StallWatchdog`.
    """
    watchdog = StallWatchdog(parent=window)
    # Dynamic attribute: window has no static slot for it (mirrors
    # run_off_thread's dynamic worker-registry pattern).
    window._stall_watchdog = watchdog  # type: ignore[attr-defined]
    watchdog.start()
    return watchdog

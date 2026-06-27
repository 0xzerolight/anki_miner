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

import faulthandler
import logging
import sys
import threading
import time
import traceback

from PyQt6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

# Module-global stall counter so the e2e harness (and tests) can read totals
# without holding a handle to whichever StallWatchdog instance is live.
_global_stall_count = 0
_global_lock = threading.Lock()


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

    def stop(self) -> None:
        """Stop the heartbeat timer, signal the monitor to exit, and join it.

        Safe to call if never started or more than once.
        """
        self._stop_event.set()

        if self._timer is not None:
            self._timer.stop()
            self._timer.timeout.disconnect(self._on_heartbeat)
            self._timer = None

        monitor = self._monitor_thread
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=max(1.0, self._poll_ms / 1000 * 4))
        self._monitor_thread = None

    # --- Internals ---------------------------------------------------------

    def _on_heartbeat(self) -> None:
        """GUI-thread slot: stamp the shared heartbeat timestamp."""
        with self._tick_lock:
            self._last_tick = time.monotonic()
        # A fresh tick means the GUI thread is alive again; re-arm reporting.
        self._reported_current_stall = False

    def _monitor_loop(self) -> None:
        """Monitor-thread loop: detect heartbeat gaps over the threshold."""
        threshold_s = self._threshold_ms / 1000
        poll_s = self._poll_ms / 1000
        while not self._stop_event.wait(poll_s):
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
        logger.warning(
            "GUI thread stall detected: %.0f ms (threshold %d ms). Main-thread stack:\n%s",
            gap_ms,
            self._threshold_ms,
            stack_text,
        )

    def _format_main_stack(self) -> str:
        """Return the GUI thread's current stack as text, best-effort.

        Falls back to dumping all tracebacks to stderr when the frame for the
        main thread cannot be resolved.
        """
        frame = sys._current_frames().get(self._main_thread_id)
        if frame is not None:
            return "".join(traceback.format_stack(frame))
        # Frame unavailable — dump everything to stderr as a backstop.
        faulthandler.dump_traceback(file=sys.stderr)
        return "(main-thread frame unavailable; traceback dumped to stderr)"


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

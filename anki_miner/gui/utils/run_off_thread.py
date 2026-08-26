"""Helpers for running blocking work off the Qt GUI thread.

Two reusable primitives back the GUI-freeze-hardening effort:

* :func:`run_off_thread` — fire a zero-arg blocking callable on a worker
  thread and deliver its result back on the GUI thread, with automatic
  worker ownership so it is never garbage-collected mid-run.
* :func:`still_running` / :func:`join_or_retain` — deleted-wrapper-safe
  liveness and bounded joins that retain timed-out workers.

Slots connected here run on the GUI thread: the worker is parented to a
GUI-thread :class:`QObject`, so Qt queues its cross-thread signals onto the
receiver's (GUI) thread.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import QObject, QThread

from anki_miner.gui.workers.base_worker import SingleCallWorker

logger = logging.getLogger(__name__)

_WorkerT = TypeVar("_WorkerT", bound=QThread)

_REGISTRY_ATTR = "_off_thread_workers"
_DISPATCH_CLOSED_ATTR = "_off_thread_dispatch_closed"

# Process-global registry of every LIVE run_off_thread worker. Each worker is
# also tracked on its parent's _off_thread_workers set (for premature-GC
# protection), but a worker whose parent widget is being destroyed at app close
# is no longer reachable through that per-parent set. join_all_off_thread_workers
# uses this global set to cancel+join every short-lived background worker at
# shutdown so Qt never destroys a running QThread (which can abort the process).
# Entries are added on dispatch and discarded in the finished -> _teardown
# handler, exactly like the per-parent set.
#
# Invariant: every mutation of this set happens on the GUI thread — dispatch
# (run_off_thread) is called from GUI code, and the finished -> _teardown
# discard is a queued slot delivered on the GUI thread. That single-thread
# access is why the bare set needs no lock.
_LIVE_OFF_THREAD_WORKERS: set[QThread] = set()


def run_off_thread(
    parent: QObject,
    work: Callable[[], object] | Callable[[Callable[[], bool]], object],
    on_done: Callable[[object], None],
    on_error: Callable[[str], None] | None = None,
    *,
    error_prefix: str = "",
    pass_cancel_check: bool = False,
    on_finished: Callable[[], None] | None = None,
) -> SingleCallWorker:
    """Run ``work`` off the GUI thread and deliver its result on the GUI thread.

    Args:
        parent: GUI-thread QObject that owns the worker. Its
            ``_off_thread_workers`` set (created lazily) holds a live
            reference until the worker finishes, preventing premature GC.
        work: Blocking callable executed on the worker thread. With
            ``pass_cancel_check=True``, it receives a live cancellation
            predicate for checkpoints inside long work.
        on_done: Called with ``work()``'s return value on the GUI thread.
        on_error: Called with ``f"{error_prefix}{exc}"`` on failure. When
            ``None``, the error string is logged at WARNING instead.
        error_prefix: Prepended to the exception text on failure.
        pass_cancel_check: Pass the worker's cancellation predicate to ``work``.
        on_finished: Called on the GUI thread for every terminal outcome,
            including cancellation, before worker teardown.

    Returns:
        The started :class:`SingleCallWorker` (callers may keep it to
        ``cancel()``).

        **Dispatch-closed contract:** if the parent's application tree is
        closing (set via :func:`close_off_thread_dispatch`), returns an already-
        cancelled worker that never started. ``on_done`` will never fire.
        Callers must treat this return value as a no-op.
    """
    worker = SingleCallWorker(
        work,
        error_prefix=error_prefix,
        pass_cancel_check=pass_cancel_check,
        parent=parent,
    )

    if _dispatch_closed(parent):
        worker.cancel()
        logger.debug("off-thread dispatch rejected during shutdown")
        return worker

    worker.result_ready.connect(on_done)
    if on_error is None:
        # SingleCallWorker.report_failure already logged this at the level its
        # type deserves; a second record here only duplicated it (and paired a
        # WARNING with a spurious traceback for a typed domain failure). Kept at
        # DEBUG so "nobody handled this" is still visible.
        worker.error.connect(lambda msg: logger.debug("off-thread work failed, no handler: %s", msg))
    else:
        worker.error.connect(on_error)
    if on_finished is not None:
        worker.finished.connect(on_finished)

    registry = _get_registry(parent)
    registry.add(worker)
    _LIVE_OFF_THREAD_WORKERS.add(worker)

    def _teardown() -> None:
        registry.discard(worker)
        _LIVE_OFF_THREAD_WORKERS.discard(worker)
        # The worker's underlying C++ object may already be destroyed (e.g. the
        # parent widget was torn down while the work was still in flight, so Qt
        # deleted the child worker before this queued slot ran). Nothing left to
        # schedule for deletion in that case — suppress the RuntimeError.
        with contextlib.suppress(RuntimeError):
            worker.deleteLater()

    # finished fires after result_ready/error, so result/error and the optional
    # terminal callback run before teardown.
    worker.finished.connect(_teardown)

    worker.start()
    return worker


def close_off_thread_dispatch(root: QObject) -> None:
    """Reject new off-thread work owned by ``root`` or its descendants."""
    setattr(root, _DISPATCH_CLOSED_ATTR, True)


def _dispatch_closed(parent: QObject) -> bool:
    """Return whether ``parent`` belongs to an application tree closing down."""
    current: QObject | None = parent
    while current is not None:
        if bool(getattr(current, _DISPATCH_CLOSED_ATTR, False)):
            return True
        try:
            current = current.parent()
        except RuntimeError:
            return True
    return False


def still_running(worker: QThread | None) -> bool:
    """Return whether ``worker`` has a live, running C++ QThread."""
    if worker is None:
        return False
    try:
        return bool(worker.isRunning())
    except RuntimeError:
        return False


def join_or_retain(
    worker: _WorkerT | None,
    timeout_ms: int = 2000,
    *,
    cancel_worker: bool = True,
) -> _WorkerT | None:
    """Bounded-join ``worker``; return it only while it remains live."""
    if not still_running(worker):
        return None
    assert worker is not None
    try:
        if cancel_worker:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
        if worker.wait(timeout_ms):
            return None
    except RuntimeError:
        return None
    return worker if still_running(worker) else None


def join_worker(worker: QThread | None, timeout_ms: int = 2000) -> bool:
    """Bounded, GUI-safe join. Never waits without a timeout.

    Args:
        worker: The thread to join, or ``None``.
        timeout_ms: Maximum time to wait, in milliseconds.

    Returns:
        ``True`` if the worker is gone (None / not running) or stopped within
        the timeout; ``False`` if it was still running when the timeout
        elapsed.
    """
    return join_or_retain(worker, timeout_ms) is None


def join_tracked_workers(parent: QObject, timeout_ms: int = 2000) -> list[QThread]:
    """Join all workers tracked on ``parent`` at teardown, best-effort.

    Each tracked worker is joined via :func:`join_worker`; those that stop are
    dropped from the tracking set. Workers whose underlying C++ object has
    already been deleted (raising ``RuntimeError``) are silently dropped.

    Args:
        parent: QObject whose ``_off_thread_workers`` set is drained.
        timeout_ms: Per-worker join timeout, in milliseconds.

    Returns:
        The workers that did NOT stop within the timeout, so the caller can
        decide whether to defer close.
    """
    registry = _get_registry(parent)
    laggards: list[QThread] = []

    for worker in list(registry):
        try:
            if join_worker(worker, timeout_ms):
                registry.discard(worker)
            else:
                laggards.append(worker)
        except RuntimeError:
            # Underlying C++ object already deleted — treat as gone.
            registry.discard(worker)

    return laggards


def join_all_off_thread_workers(timeout_ms: int = 2000) -> list[QThread]:
    """Cancel + bounded-join every LIVE run_off_thread worker at app close.

    Drains the process-global :data:`_LIVE_OFF_THREAD_WORKERS` set: each worker
    is cancelled (if cooperative) and joined via :func:`join_worker`; those that
    stop are dropped from the global set. Workers whose underlying C++ object has
    already been deleted (raising ``RuntimeError``) are silently dropped, exactly
    as :func:`join_tracked_workers` does.

    This is the single place that reaps the short-lived background workers
    dispatched by widgets across the app (analytics refresh, settings-panel
    registry scans, ffprobe/ASR probes) that are otherwise destroyed mid-run
    when their parent widget is torn down at close — Qt destroying a running
    QThread can abort the process.

    Args:
        timeout_ms: Per-worker join timeout, in milliseconds.

    Returns:
        The workers that did NOT stop within the timeout, so the caller can fold
        them into its deferred-close path.
    """
    laggards: list[QThread] = []

    for worker in list(_LIVE_OFF_THREAD_WORKERS):
        try:
            if join_worker(worker, timeout_ms):
                _LIVE_OFF_THREAD_WORKERS.discard(worker)
            else:
                laggards.append(worker)
        except RuntimeError:
            # Underlying C++ object already deleted — treat as gone.
            _LIVE_OFF_THREAD_WORKERS.discard(worker)

    return laggards


def _get_registry(parent: QObject) -> set[QThread]:
    """Return ``parent``'s lazily-created worker tracking set."""
    registry: set[QThread] | None = getattr(parent, _REGISTRY_ATTR, None)
    if registry is None:
        registry = set()
        setattr(parent, _REGISTRY_ATTR, registry)
    return registry

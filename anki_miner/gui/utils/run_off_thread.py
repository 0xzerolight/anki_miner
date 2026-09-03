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

Every dispatch is named. :func:`describe_work` turns the parent object and the
blocking callable into one ``Parent.work`` identity that becomes the worker's
log context and thread name, so its start receipt and any failure name a call
site. Without it every dispatch logs as an anonymous ``SingleCallWorker`` -- the
same line for the analytics refresh, an ffprobe, and a registry scan -- and "the
button does nothing" cannot be traced back to a callable.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import QObject, QThread

from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.utils.logging_ext import capped, log_summary, suppressed

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

# Depth guard for partial-unwrapping: a hand-built cycle of partials must not
# spin forever inside a logging helper.
_MAX_PARTIAL_DEPTH = 8


def _callable_name(work: object) -> str:
    """Return the most specific readable name for ``work``.

    ``functools.partial`` wrappers are unwrapped to the callable they bind,
    since a partial has no name of its own. Enclosing-scope noise is dropped
    from a qualified name (``test_x.<locals>.load_decks`` -> ``load_decks``),
    and a callable object with neither ``__qualname__`` nor ``__name__`` falls
    back to its type.
    """
    target = work
    for _ in range(_MAX_PARTIAL_DEPTH):
        if not isinstance(target, functools.partial):
            break
        target = target.func
    name = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if not name:
        return type(target).__name__
    if "<locals>." in name:
        name = name.rsplit("<locals>.", 1)[1]
    return str(name)


def describe_work(work: object, parent: object) -> str:
    """Return the ``Parent.work`` identity used as a dispatch's log context.

    The parent type is the prefix because the callable alone is rarely enough:
    much of the app's off-thread work is a one-line lambda or a helper named
    ``_scan``. A bound method whose qualified name already starts with the
    parent's class is not prefixed twice.
    """
    name = _callable_name(work)
    parent_name = type(parent).__name__
    if name == parent_name or name.startswith(f"{parent_name}."):
        return name
    return f"{parent_name}.{name}"


def worker_context(worker: object) -> str:
    """Return a worker's log context, falling back to its class name.

    Workers dispatched through :func:`run_off_thread` carry the
    :func:`describe_work` identity in ``_context``; workers constructed
    directly do not, and their class name is the best label available.
    """
    context = getattr(worker, "_context", None)
    return str(context) if context else type(worker).__name__


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
    context = describe_work(work, parent)
    worker = SingleCallWorker(
        work,
        error_prefix=error_prefix,
        pass_cancel_check=pass_cancel_check,
        context=context,
        parent=parent,
    )

    if _dispatch_closed(parent):
        worker.cancel()
        # WARNING, not DEBUG: the continuation never runs, so whatever the user
        # clicked silently did nothing. Naming the parent and the work is what
        # turns that report into a call site.
        log_summary(
            logger,
            "Off-thread dispatch rejected during shutdown",
            level=logging.WARNING,
            parent=type(parent).__name__,
            work=_callable_name(work),
        )
        return worker

    worker.result_ready.connect(on_done)
    if on_error is None:
        # SingleCallWorker.report_failure already logged this at the level its
        # type deserves; a second record here only duplicated it (and paired a
        # WARNING with a spurious traceback for a typed domain failure). Kept at
        # DEBUG so "nobody handled this" is still visible.
        worker.error.connect(lambda msg: logger.debug("off-thread work failed, no handler: %s: %s", context, msg))
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
        # schedule for deletion in that case — record it and carry on.
        with suppressed(logger, f"deleteLater for {context}"):
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


def _wrapper_deleted(worker: QThread) -> bool:
    """Whether ``worker``'s underlying C++ object is already gone.

    Probed explicitly rather than left to the ``except RuntimeError`` arms
    below: :func:`join_worker` swallows the RuntimeError internally and reports
    the worker as stopped, so those arms alone would drop a deleted worker with
    no record at all. The redundant ``except`` stays as the belt for a
    RuntimeError raised anywhere else in the loop body.
    """
    try:
        worker.isRunning()
    except RuntimeError:
        return True
    return False


def _log_deleted_worker(worker: QThread) -> None:
    """Record that a tracked worker's wrapper was already destroyed."""
    logger.debug("Off-thread worker already deleted: context=%s", worker_context(worker))


def _log_laggards(laggards: list[QThread], **fields: object) -> None:
    """Warn once per join call about the workers that outlived their timeout.

    One line, not one per worker: a shutdown join can hold a dozen of them and
    the count plus the contexts is the whole diagnosis. The contexts are what
    make "the app took ten seconds to close" attributable.
    """
    if not laggards:
        return
    log_summary(
        logger,
        "Off-thread workers still running",
        level=logging.WARNING,
        count=len(laggards),
        workers=capped(worker_context(worker) for worker in laggards),
        **fields,
    )


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
            if _wrapper_deleted(worker):
                registry.discard(worker)
                _log_deleted_worker(worker)
            elif join_worker(worker, timeout_ms):
                registry.discard(worker)
            else:
                laggards.append(worker)
        except RuntimeError:
            # Underlying C++ object already deleted — treat as gone.
            registry.discard(worker)
            _log_deleted_worker(worker)

    _log_laggards(laggards, parent=type(parent).__name__)
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
            if _wrapper_deleted(worker):
                _LIVE_OFF_THREAD_WORKERS.discard(worker)
                _log_deleted_worker(worker)
            elif join_worker(worker, timeout_ms):
                _LIVE_OFF_THREAD_WORKERS.discard(worker)
            else:
                laggards.append(worker)
        except RuntimeError:
            # Underlying C++ object already deleted — treat as gone.
            _LIVE_OFF_THREAD_WORKERS.discard(worker)
            _log_deleted_worker(worker)

    _log_laggards(laggards)
    return laggards


def _get_registry(parent: QObject) -> set[QThread]:
    """Return ``parent``'s lazily-created worker tracking set."""
    registry: set[QThread] | None = getattr(parent, _REGISTRY_ATTR, None)
    if registry is None:
        registry = set()
        setattr(parent, _REGISTRY_ATTR, registry)
    return registry

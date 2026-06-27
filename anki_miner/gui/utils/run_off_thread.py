"""Helpers for running blocking work off the Qt GUI thread.

Two reusable primitives back the GUI-freeze-hardening effort:

* :func:`run_off_thread` — fire a zero-arg blocking callable on a worker
  thread and deliver its result back on the GUI thread, with automatic
  worker ownership so it is never garbage-collected mid-run.
* :func:`join_worker` / :func:`join_tracked_workers` — bounded, GUI-safe
  joins that replace every untimed ``worker.wait()`` (which can hang the GUI
  thread forever).

Slots connected here run on the GUI thread: the worker is parented to a
GUI-thread :class:`QObject`, so Qt queues its cross-thread signals onto the
receiver's (GUI) thread.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, QThread

from anki_miner.gui.workers.base_worker import SingleCallWorker

logger = logging.getLogger(__name__)

_REGISTRY_ATTR = "_off_thread_workers"


def run_off_thread(
    parent: QObject,
    work: Callable[[], object],
    on_done: Callable[[object], None],
    on_error: Callable[[str], None] | None = None,
    *,
    error_prefix: str = "",
) -> SingleCallWorker:
    """Run ``work`` off the GUI thread and deliver its result on the GUI thread.

    Args:
        parent: GUI-thread QObject that owns the worker. Its
            ``_off_thread_workers`` set (created lazily) holds a live
            reference until the worker finishes, preventing premature GC.
        work: Zero-arg blocking callable executed on the worker thread.
        on_done: Called with ``work()``'s return value on the GUI thread.
        on_error: Called with ``f"{error_prefix}{exc}"`` on failure. When
            ``None``, the error string is logged at WARNING instead.
        error_prefix: Prepended to the exception text on failure.

    Returns:
        The started :class:`SingleCallWorker` (callers may keep it to
        ``cancel()``).
    """
    worker = SingleCallWorker(work, error_prefix=error_prefix, parent=parent)

    worker.result_ready.connect(on_done)
    if on_error is None:
        worker.error.connect(lambda msg: logger.warning("off-thread work failed: %s", msg))
    else:
        worker.error.connect(on_error)

    registry = _get_registry(parent)
    registry.add(worker)

    def _teardown() -> None:
        registry.discard(worker)
        # The worker's underlying C++ object may already be destroyed (e.g. the
        # parent widget was torn down while the work was still in flight, so Qt
        # deleted the child worker before this queued slot ran). Nothing left to
        # schedule for deletion in that case — suppress the RuntimeError.
        with contextlib.suppress(RuntimeError):
            worker.deleteLater()

    # finished fires after result_ready/error, so the result slots still run.
    worker.finished.connect(_teardown)

    worker.start()
    return worker


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
    if worker is None or not worker.isRunning():
        return True

    # Give cooperative (CancellableWorker) loops a chance to exit early.
    cancel = getattr(worker, "cancel", None)
    if callable(cancel):
        cancel()

    return bool(worker.wait(timeout_ms))


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


def _get_registry(parent: QObject) -> set:
    """Return ``parent``'s lazily-created worker tracking set."""
    registry = getattr(parent, _REGISTRY_ATTR, None)
    if registry is None:
        registry = set()
        setattr(parent, _REGISTRY_ATTR, registry)
    return registry

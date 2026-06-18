"""Base class for cancellable worker threads."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anki_miner.orchestration.episode_processor import EpisodeProcessor


class CancellableWorker(QThread):
    """Base class for worker threads that support cancellation.

    Subclasses should:
    1. Call check_cancelled() at appropriate checkpoints in run()
    2. Stop processing when check_cancelled() returns True
    3. Emit error signal for exceptions (already defined here)

    Uses threading.Event for thread-safe cancellation flag.
    """

    # Signal emitted when an error occurs during processing
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation of the worker.

        This sets a thread-safe flag. The worker should check this flag
        at appropriate points and stop processing gracefully.
        """
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested.

        Returns:
            True if cancel() has been called
        """
        return self._cancel_event.is_set()

    def check_cancelled(self) -> bool:
        """Check if worker should stop processing.

        Call this at checkpoints in run(). If it returns True,
        stop processing and return from run().

        Returns:
            True if cancellation was requested
        """
        return self._cancel_event.is_set()


class ProcessorOwningWorker(CancellableWorker):
    """Base for mining workers that drive an :class:`EpisodeProcessor`.

    Declares the single typed contract for "the processor that owns
    curation/lookup resources". GUI readers — the curation dialog context
    builders and the Settings → Remove dictionary release hooks — consume
    only :attr:`curation_processor`, so a worker-side rename is a mypy
    error at the reader instead of a silent ``getattr`` miss (lookup_fn
    degrading to None, sqlite handles never closed).

    Subclasses MUST override the property to return their own processor
    storage (constructor arg or per-item current processor).
    """

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """Processor owning dictionary resources for the current or most recent run.

        ``None`` only when the worker has not created/received a processor
        yet (e.g. a queue worker before its first item).
        """
        raise NotImplementedError("ProcessorOwningWorker subclasses must override curation_processor")


class SingleCallWorker(CancellableWorker):
    """Run one blocking callable off the GUI thread and emit its result.

    Generic short-lived worker for "call a service method once, hand the
    result back on the main thread" tasks (e.g. the AnkiConnect fetchers).
    Cancellation is honored before the call and before the emit, so a
    cancel()'d worker stays silent.

    The result is carried as ``object`` so Qt's metatype system doesn't need a
    custom registration for whatever the callable returns. On failure, the
    inherited ``error`` signal carries ``f"{error_prefix}{exc}"``.
    """

    # Carries the callable's return value — typed as object (see class docstring).
    result_ready = pyqtSignal(object)

    def __init__(self, work: Callable[[], object], *, error_prefix: str = "", parent=None) -> None:
        """Initialize the single-call worker.

        Args:
            work: Zero-arg callable executed in the background thread.
            error_prefix: Prepended to the exception text on the error signal.
            parent: Optional parent QObject for lifetime management.
        """
        super().__init__(parent)
        self._work = work
        self._error_prefix = error_prefix

    def run(self) -> None:
        """Execute the callable in the background thread and emit the result."""
        try:
            if self.check_cancelled():
                return

            result = self._work()

            if not self.check_cancelled():
                self.result_ready.emit(result)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("SingleCallWorker unhandled exception")
            if not self.check_cancelled():
                self.error.emit(f"{self._error_prefix}{e}")

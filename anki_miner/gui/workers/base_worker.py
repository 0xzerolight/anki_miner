"""Base class for cancellable worker threads."""

import threading
from collections.abc import Callable

from PyQt6.QtCore import QThread, pyqtSignal


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
            if not self.check_cancelled():
                self.error.emit(f"{self._error_prefix}{e}")

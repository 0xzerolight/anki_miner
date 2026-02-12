"""Base class for cancellable worker threads."""

import threading

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

    def reset_cancellation(self) -> None:
        """Reset the cancellation flag.

        Call this if reusing a worker instance (not recommended).
        """
        self._cancel_event.clear()

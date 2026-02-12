"""GUI progress callback implementation using Qt signals for thread-safe communication."""

from PyQt6.QtCore import QObject, pyqtSignal


class GUIProgressCallback(QObject):
    """Thread-safe progress callback using Qt signals.

    Implements ProgressCallback protocol through structural subtyping (duck typing).
    This avoids metaclass conflicts between QObject and Protocol metaclasses.

    Worker threads can call callback methods (on_start, on_progress, etc.) which
    emit Qt signals that are automatically queued for the main GUI thread to update
    progress bars and status indicators.
    """

    # Signals for thread-safe communication
    start_signal = pyqtSignal(int, str)  # total, description
    progress_signal = pyqtSignal(int, str)  # current, item_description
    complete_signal = pyqtSignal()
    error_signal = pyqtSignal(str, str)  # item_description, error_message

    def __init__(self, parent=None):
        """Initialize the GUI progress callback.

        Args:
            parent: Optional parent QObject
        """
        super().__init__(parent)

    def on_start(self, total: int, description: str) -> None:
        """Called when an operation starts.

        Args:
            total: Total number of items to process
            description: Description of the operation
        """
        self.start_signal.emit(total, description)

    def on_progress(self, current: int, item_description: str) -> None:
        """Called when an item is processed.

        Args:
            current: Current item number (1-based)
            item_description: Description of the current item
        """
        self.progress_signal.emit(current, item_description)

    def on_complete(self) -> None:
        """Called when an operation completes successfully."""
        self.complete_signal.emit()

    def on_error(self, item_description: str, error_message: str) -> None:
        """Called when an item fails during processing.

        Args:
            item_description: Description of the failed item
            error_message: Error message explaining the failure
        """
        self.error_signal.emit(item_description, error_message)

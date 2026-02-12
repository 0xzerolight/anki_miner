"""Worker thread for system validation."""

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services import ValidationService


class ValidationWorkerThread(CancellableWorker):
    """Worker thread for system validation.

    This thread runs validation checks in the background to avoid blocking
    the GUI during startup or when user requests validation.

    Supports cancellation via the base class cancel() method.
    """

    result_ready = pyqtSignal(object)  # ValidationResult

    def __init__(self, validator: ValidationService, parent=None):
        """Initialize the validation worker thread.

        Args:
            validator: Validation service instance
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self.validator = validator

    def run(self) -> None:
        """Execute validation in background thread."""
        try:
            if self.check_cancelled():
                return

            result = self.validator.validate_setup()

            if not self.check_cancelled():
                self.result_ready.emit(result)
        except Exception as e:
            if not self.check_cancelled():
                error_msg = f"Error during validation: {str(e)}"
                self.error.emit(error_msg)

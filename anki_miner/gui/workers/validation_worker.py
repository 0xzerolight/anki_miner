"""Worker thread for system validation."""

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services import ValidationService
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


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
        self.log_start("ValidationWorkerThread", validator=type(self.validator).__name__)
        try:
            if self.check_cancelled():
                return

            result = self.validator.validate_setup()

            if not self.check_cancelled():
                self.result_ready.emit(result)
                log_summary(
                    logger,
                    "ValidationWorkerThread done",
                    issues=len(result.issues),
                    errors=len(result.get_errors()),
                    warnings=len(result.get_warnings()),
                    all_passed=result.all_passed,
                )
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            self.report_failure(
                e,
                context="ValidationWorkerThread",
                on_error=lambda msg: self.error.emit(f"Error during validation: {msg}"),
            )

"""Worker thread for checking application updates."""

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.update_checker import UpdateChecker
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


class UpdateWorkerThread(CancellableWorker):
    """Worker thread for checking updates in the background.

    Emits ``result_ready`` with an :class:`~anki_miner.services.update_checker.UpdateInfo`
    when a newer release is available, or with ``None`` when there is no update or
    the check failed (network error, etc.). The signal is typed as ``object`` so
    Qt can carry either payload across the thread boundary.
    """

    # Carries UpdateInfo | None — typed as object so Qt's metatype system
    # accepts both the dataclass and None without registering a custom type.
    result_ready = pyqtSignal(object)

    def __init__(self, checker: UpdateChecker, parent=None):
        """Initialize the update worker thread.

        Args:
            checker: UpdateChecker service instance
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self.checker = checker

    def run(self) -> None:
        """Execute update check in background thread."""
        self.log_start("UpdateWorkerThread", checker=type(self.checker).__name__)
        try:
            if self.check_cancelled():
                return

            info = self.checker.check_for_update()

            if not self.check_cancelled():
                # Always emit (info may be None) so the main-thread slot can
                # take the single config-write code path either way.
                self.result_ready.emit(info)
                log_summary(logger, "UpdateWorkerThread done", results=1, update_available=info is not None)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            self.report_failure(
                e,
                context="UpdateWorkerThread",
                on_error=lambda msg: self.error.emit(f"Error checking for updates: {msg}"),
            )

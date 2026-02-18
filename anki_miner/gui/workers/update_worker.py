"""Worker thread for checking application updates."""

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.update_checker import UpdateChecker


class UpdateWorkerThread(CancellableWorker):
    """Worker thread for checking updates in the background.

    Emits result_ready with (update_available, latest_version, release_url)
    or emits nothing if the check fails or is cancelled.
    """

    result_ready = pyqtSignal(bool, str, str)  # update_available, version, url

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
        try:
            if self.check_cancelled():
                return

            result = self.checker.check_for_update()

            if not self.check_cancelled() and result is not None:
                update_available, latest_version, release_url = result
                self.result_ready.emit(update_available, latest_version, release_url)
        except Exception as e:
            if not self.check_cancelled():
                self.error.emit(f"Error checking for updates: {e}")

"""Worker thread for the yt-dlp auto-download / self-update."""

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.ytdlp_updater import YtdlpUpdater

logger = logging.getLogger(__name__)


class YtdlpUpdateWorker(CancellableWorker):
    """Run :meth:`YtdlpUpdater.check_and_update` off the GUI thread.

    Emits ``result_ready`` with the :class:`~anki_miner.services.ytdlp_updater.YtdlpUpdateResult`
    (typed as ``object`` so Qt carries it without a custom metatype). On an
    unexpected exception it emits the inherited ``error`` signal instead of
    propagating — the updater already never raises, so this is belt-and-braces.
    """

    # Carries YtdlpUpdateResult — typed as object (see class docstring).
    result_ready = pyqtSignal(object)

    def __init__(self, updater: YtdlpUpdater, *, force: bool, parent=None) -> None:
        """Initialize the worker.

        Args:
            updater: The yt-dlp updater service instance.
            force: When True, bypass the 24h throttle (manual "Update now").
            parent: Optional parent QObject for lifetime management.
        """
        super().__init__(parent)
        self._updater = updater
        self._force = force

    def run(self) -> None:
        """Execute the throttled check + (if newer) install in the background."""
        try:
            if self.check_cancelled():
                return

            result = self._updater.check_and_update(force=self._force, cancel=self.check_cancelled)

            if not self.check_cancelled():
                self.result_ready.emit(result)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("YtdlpUpdateWorker unhandled exception")
            if not self.check_cancelled():
                self.error.emit(f"yt-dlp update error: {e}")

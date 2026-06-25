"""Worker that downloads and installs the alass binary in the background.

The download is a single streamed transfer of indeterminate duration, so this
worker emits plain status text rather than progress percentages.

Signal contract (mirrors ``AsrModelDownloadWorker``):
    ``status(str)``              — informational status during the install
    ``result_ready(bool, str)``  — (ok, message) when the install completes or fails

The result is carried on ``result_ready`` rather than ``finished`` so the
inherited ``QThread.finished`` (0-arg, fires on real thread exit including the
cancel path) stays intact for lifecycle release — matching AsrModelDownloadWorker,
ValidationWorkerThread, UpdateWorkerThread, and YtdlpUpdateWorker.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.alass_installer import install_alass

logger = logging.getLogger(__name__)


class AlassInstallWorker(CancellableWorker):
    """Download and install the alass binary in a background thread.

    Args:
        bin_root: Managed directory for downloaded executables; typically
            ``config.bin_root``.
        parent: Optional parent QObject.
    """

    #: Informational status message emitted during the install.
    status = pyqtSignal(str)
    #: Emitted when the install completes (ok=True) or fails (ok=False).
    #: The second argument is a human-readable message. Distinct from the
    #: inherited ``QThread.finished`` so the latter stays free for release.
    result_ready = pyqtSignal(bool, str)

    def __init__(self, bin_root: Path, parent=None) -> None:
        """Initialise the install worker."""
        super().__init__(parent)
        self._bin_root = bin_root

    def run(self) -> None:
        """Execute the install in the background thread.

        Emits ``status`` before starting, then calls ``install_alass``. Any
        exception is caught and forwarded as ``result_ready(False, error_text)``.
        The download is indeterminate — no fake percentage is emitted.
        """
        if self.check_cancelled():
            return

        self.status.emit(self.tr("Downloading alass…"))

        try:
            install_alass(self._bin_root, cancel_event=self._cancel_event)
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("alass install failed")
            if not self.check_cancelled():
                self.result_ready.emit(False, str(exc))
            return

        if not self.check_cancelled():
            self.result_ready.emit(True, self.tr("alass installed successfully."))

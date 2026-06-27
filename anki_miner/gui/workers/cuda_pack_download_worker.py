"""Worker that downloads and installs the cuDNN + cuBLAS library pack.

The pack is two large wheels; their byte totals are known up front (the
``Content-Length`` headers), so this worker converts the underlying download
progress into a percentage status line — unlike the indeterminate ASR model and
alass workers.

Signal contract (mirrors ``AlassInstallWorker`` / ``AsrModelDownloadWorker``):
    ``status(str)``              — informational status during the install
    ``result_ready(bool, str)``  — (ok, message) when the install completes or fails

The result is carried on ``result_ready`` rather than ``finished`` so the
inherited ``QThread.finished`` (0-arg, fires on real thread exit including the
cancel path) stays intact for lifecycle release — matching AlassInstallWorker,
AsrModelDownloadWorker, ValidationWorkerThread, and UpdateWorkerThread.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.asr.cuda_pack_installer import install_cuda_pack
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class CudaPackDownloadWorker(CancellableWorker):
    """Download and install the CUDA library pack in a background thread.

    Args:
        cuda_libs_root: Managed directory for the downloaded libs; typically
            ``config.cuda_libs_root``.
        parent: Optional parent QObject.
    """

    #: Informational status message emitted during the install.
    status = pyqtSignal(str)
    #: Emitted when the install completes (ok=True) or fails (ok=False).
    #: The second argument is a human-readable message. Distinct from the
    #: inherited ``QThread.finished`` so the latter stays free for release.
    result_ready = pyqtSignal(bool, str)

    def __init__(self, cuda_libs_root: Path, parent=None) -> None:
        """Initialise the download worker."""
        super().__init__(parent)
        self._cuda_libs_root = cuda_libs_root

    def _on_progress(self, downloaded: int, total: int, message: str) -> None:
        """Convert ``(downloaded, total, message)`` into a human status line."""
        if total > 0:
            pct = min(100, int(downloaded * 100 / total))
            self.status.emit(tr_format(self.tr("%1 (%2%)"), message, str(pct)))
        else:
            self.status.emit(message)

    def run(self) -> None:
        """Execute the install in the background thread.

        Emits ``status`` before starting, then calls ``install_cuda_pack`` with
        a progress adapter that surfaces a percentage. Any exception is caught
        and forwarded as ``result_ready(False, error_text)``.
        """
        if self.check_cancelled():
            return

        self.status.emit(self.tr("Downloading GPU libraries…"))

        try:
            install_cuda_pack(
                self._cuda_libs_root,
                progress=self._on_progress,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("CUDA library pack install failed")
            if not self.check_cancelled():
                self.result_ready.emit(False, str(exc))
            return

        if not self.check_cancelled():
            self.result_ready.emit(True, self.tr("GPU libraries installed successfully."))

"""Worker that downloads a faster-whisper ASR model in the background.

HF model downloads are indeterminate (shard sizes unknown up front), so
this worker emits plain status text rather than progress percentages.

Signal contract (mirrors the spec in ``model_manager`` module docstring):
    ``status(str)``           — informational status during the download
    ``result_ready(bool, str)``  — (ok, message) when the download completes or fails

The result is carried on ``result_ready`` rather than ``finished`` so the
inherited ``QThread.finished`` (0-arg, fires on real thread exit including the
cancel path) stays intact for lifecycle release — matching ValidationWorkerThread,
UpdateWorkerThread, and YtdlpUpdateWorker.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.asr import model_manager
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class AsrModelDownloadWorker(CancellableWorker):
    """Download a faster-whisper model in a background thread.

    Args:
        name: Model identifier (e.g. ``"large-v3"`` or ``"small"``).
        models_root: Directory where model weights will be stored;
            typically ``config.asr_models_root``.
        parent: Optional parent QObject.
    """

    #: Informational status message emitted during the download.
    status = pyqtSignal(str)
    #: Emitted when the download completes (ok=True) or fails (ok=False).
    #: The second argument is a human-readable message. Distinct from the
    #: inherited ``QThread.finished`` so the latter stays free for release.
    result_ready = pyqtSignal(bool, str)

    def __init__(self, name: str, models_root: Path, parent=None) -> None:
        """Initialise the download worker."""
        super().__init__(parent)
        self._name = name
        self._models_root = models_root

    def run(self) -> None:
        """Execute the download in the background thread.

        Emits ``status`` before starting, then calls
        ``model_manager.download``.  Any exception is caught and forwarded
        as ``result_ready(False, error_text)``.  HF progress is indeterminate —
        no fake percentage is emitted.
        """
        if self.check_cancelled():
            return

        self.status.emit(tr_format(self.tr("Downloading %1…"), self._name))

        try:
            model_manager.download(
                self._name,
                self._models_root,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("ASR model download failed: %s", self._name)
            if not self.check_cancelled():
                self.result_ready.emit(False, str(exc))
            return

        if not self.check_cancelled():
            self.result_ready.emit(True, tr_format(self.tr("%1 downloaded successfully."), self._name))

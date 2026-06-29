"""Worker that downloads the whisper.cpp (Vulkan/CPU) ggml model + Silero VAD.

One action fetches BOTH files the whisper.cpp backend loads off disk: the
selected acoustic ggml model and the shared Silero VAD ggml file. Each file's
byte total is known up front (the ``Content-Length`` header), so the underlying
download progress is converted into a percentage status line — mirroring
``CudaPackDownloadWorker``.

Signal contract (mirrors ``CudaPackDownloadWorker`` / ``AsrModelDownloadWorker``):
    ``status(str)``              — informational status during the install
    ``result_ready(bool, str)``  — (ok, message) when the install completes or fails

The result is carried on ``result_ready`` rather than ``finished`` so the
inherited ``QThread.finished`` (0-arg, fires on real thread exit including the
cancel path) stays intact for lifecycle release — matching CudaPackDownloadWorker,
AsrModelDownloadWorker, ValidationWorkerThread, and UpdateWorkerThread.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.asr.ggml_model_installer import install_ggml_model, install_vad_model
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class VulkanModelDownloadWorker(CancellableWorker):
    """Download the acoustic ggml model + Silero VAD in a background thread.

    Args:
        asr_model: Acoustic model identifier (e.g. ``"large-v3"`` / ``"small"``);
            typically the panel's ``get_model()``.
        asr_models_root: Managed directory for the ggml files; typically
            ``config.asr_models_root``.
        parent: Optional parent QObject.
    """

    #: Informational status message emitted during the install.
    status = pyqtSignal(str)
    #: Emitted when the install completes (ok=True) or fails (ok=False).
    #: The second argument is a human-readable message. Distinct from the
    #: inherited ``QThread.finished`` so the latter stays free for release.
    result_ready = pyqtSignal(bool, str)

    def __init__(self, asr_model: str, asr_models_root: Path, parent=None) -> None:
        """Initialise the download worker."""
        super().__init__(parent)
        self._asr_model = asr_model
        self._asr_models_root = asr_models_root

    def _on_progress(self, downloaded: int, total: int, message: str) -> None:
        """Convert ``(downloaded, total, message)`` into a human status line."""
        if total > 0:
            pct = min(100, int(downloaded * 100 / total))
            self.status.emit(tr_format(self.tr("%1 (%2%)"), message, str(pct)))
        else:
            self.status.emit(message)

    def run(self) -> None:
        """Execute the install in the background thread.

        Emits ``status`` before starting, then downloads the acoustic ggml
        model and the Silero VAD in turn, with a progress adapter that surfaces
        a percentage. Cancellation between the two installs short-circuits the
        VAD download. Any exception is caught and forwarded as
        ``result_ready(False, error_text)``.
        """
        if self.check_cancelled():
            return

        self.status.emit(self.tr("Downloading Vulkan model…"))

        try:
            install_ggml_model(
                self._asr_model,
                self._asr_models_root,
                progress=self._on_progress,
                cancel_event=self._cancel_event,
            )
            if self.check_cancelled():
                return
            install_vad_model(
                self._asr_models_root,
                progress=self._on_progress,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("Vulkan model install failed")
            if not self.check_cancelled():
                self.result_ready.emit(False, str(exc))
            return

        if not self.check_cancelled():
            self.result_ready.emit(True, self.tr("Vulkan model installed successfully."))

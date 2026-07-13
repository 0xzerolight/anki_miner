"""One parametrized install/download worker + the five per-resource tasks.

Collapses the ex-quintuplet of near-identical worker modules (alass install,
ASR model download, CUDA pack, onnxruntime/VAD pack, Vulkan ggml model) into a
single :class:`InstallWorker` driven by a per-tool *task* callable. The workers
shared a byte-identical run() skeleton (status → install → result_ready) and, for
the three progress-reporting tools, a byte-identical ``_on_progress`` adapter;
only the starting status line, the install call(s), and the success message
differed. Those differences live in the task builders below.

Signal contract (unchanged from the five originals):
    ``status(str)``              — informational status during the install
    ``result_ready(bool, str)``  — (ok, message) when the install completes/fails

The result is carried on ``result_ready`` rather than ``finished`` so the
inherited ``QThread.finished`` (0-arg, fires on real thread exit including the
cancel path) stays free for lifecycle release — matching ValidationWorkerThread,
UpdateWorkerThread, and YtdlpUpdateWorker.

i18n: each task's translated strings are emitted via
``QCoreApplication.translate("<OriginalWorkerContext>", …)`` so they resolve
against the existing catalog entries (which still live under the pre-collapse
worker-class contexts) with zero catalog churn. The shared ``%1 (%2%)`` progress
template is byte-identical across the three original progress contexts, so one
canonical context (``CudaPackDownloadWorker``) is used for it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

#: A per-tool install step. Given the running worker (for status emits, the
#: shared progress adapter, and the cancel Event), it performs the install and
#: returns the human-readable success message emitted on ``result_ready(True, …)``.
InstallTask = Callable[["InstallWorker"], str]


class InstallWorker(CancellableWorker):
    """Run one resource install/download off the GUI thread.

    Args:
        task: Per-tool install step (see :data:`InstallTask` and the task
            builders below). Executed inside :meth:`run`; any exception it
            raises is surfaced as ``result_ready(False, error_text)``.
        parent: Optional parent QObject.
    """

    #: Informational status message emitted during the install.
    status = pyqtSignal(str)
    #: Emitted when the install completes (ok=True) or fails (ok=False).
    #: The second argument is a human-readable message. Distinct from the
    #: inherited ``QThread.finished`` so the latter stays free for release.
    result_ready = pyqtSignal(bool, str)

    def __init__(self, task: InstallTask, parent=None) -> None:
        """Initialise the install worker."""
        super().__init__(parent)
        self._task = task

    @property
    def cancel_event(self) -> threading.Event:
        """The worker's cancellation Event, forwarded to the install task."""
        return self._cancel_event

    def _on_progress(self, downloaded: int, total: int, message: str) -> None:
        """Convert ``(downloaded, total, message)`` into a human status line.

        Byte-identical to the pre-collapse progress adapter shared by the CUDA,
        onnxruntime, and Vulkan workers; the indeterminate tools (alass, ASR)
        simply never call it.
        """
        if total > 0:
            pct = min(100, int(downloaded * 100 / total))
            self.status.emit(tr_format(_progress_template(), message, str(pct)))
        else:
            self.status.emit(message)

    def run(self) -> None:
        """Execute the task in the background thread.

        Honours a pre-run cancel, runs the task, and forwards its returned
        success message as ``result_ready(True, message)``. Any exception is
        caught and forwarded as ``result_ready(False, error_text)``. A cancel
        that lands during the task suppresses both emits — matching the five
        originals — so the native ``finished`` alone drives handle release.
        """
        if self.check_cancelled():
            return

        try:
            message = self._task(self)
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("install task failed")
            if not self.check_cancelled():
                self.result_ready.emit(False, str(exc))
            return

        if not self.check_cancelled():
            self.result_ready.emit(True, message)


def _progress_template() -> str:
    """Translated ``%1 (%2%)`` progress template.

    Kept under the ``CudaPackDownloadWorker`` context: the three pre-collapse
    progress workers carried byte-identical translations for this string in
    every catalog, so any one of them resolves correctly.
    """
    return QCoreApplication.translate("CudaPackDownloadWorker", "%1 (%2%)")


def alass_install_task(bin_root: Path) -> InstallTask:
    """Task: download + install the alass binary (indeterminate, no progress)."""

    def _task(worker: InstallWorker) -> str:
        from anki_miner.services.alass_installer import install_alass

        worker.status.emit(QCoreApplication.translate("AlassInstallWorker", "Downloading alass…"))
        install_alass(bin_root, cancel_event=worker.cancel_event)
        return QCoreApplication.translate("AlassInstallWorker", "alass installed successfully.")

    return _task


def asr_download_task(model_name: str, models_root: Path) -> InstallTask:
    """Task: download a faster-whisper model (indeterminate, no progress)."""

    def _task(worker: InstallWorker) -> str:
        from anki_miner.services.asr import model_manager

        worker.status.emit(
            tr_format(QCoreApplication.translate("AsrModelDownloadWorker", "Downloading %1…"), model_name)
        )
        model_manager.download(model_name, models_root, cancel_event=worker.cancel_event)
        return tr_format(
            QCoreApplication.translate("AsrModelDownloadWorker", "%1 downloaded successfully."), model_name
        )

    return _task


def cuda_pack_task(cuda_libs_root: Path) -> InstallTask:
    """Task: download + install the cuDNN + cuBLAS pack (percentage progress)."""

    def _task(worker: InstallWorker) -> str:
        from anki_miner.services.asr.cuda_pack_installer import install_cuda_pack

        worker.status.emit(QCoreApplication.translate("CudaPackDownloadWorker", "Downloading GPU libraries…"))
        install_cuda_pack(cuda_libs_root, progress=worker._on_progress, cancel_event=worker.cancel_event)
        return QCoreApplication.translate("CudaPackDownloadWorker", "GPU libraries installed successfully.")

    return _task


def onnx_pack_task(onnx_pack_root: Path) -> InstallTask:
    """Task: download + install the onnxruntime (Silero VAD) pack (percentage progress)."""

    def _task(worker: InstallWorker) -> str:
        from anki_miner.services.asr.onnx_pack_installer import install_onnx_pack

        worker.status.emit(QCoreApplication.translate("OnnxPackDownloadWorker", "Downloading silence-removal library…"))
        install_onnx_pack(onnx_pack_root, progress=worker._on_progress, cancel_event=worker.cancel_event)
        return QCoreApplication.translate("OnnxPackDownloadWorker", "Silence-removal library installed successfully.")

    return _task


def vulkan_model_task(asr_model: str, asr_models_root: Path) -> InstallTask:
    """Task: download the whisper.cpp ggml model + Silero VAD (percentage progress).

    One action fetches BOTH files the whisper.cpp backend loads off disk. A
    cancel landing between the two installs short-circuits the VAD download; the
    outer :meth:`InstallWorker.run` then suppresses the success emit.
    """

    def _task(worker: InstallWorker) -> str:
        from anki_miner.services.asr.ggml_model_installer import install_ggml_model, install_vad_model

        worker.status.emit(QCoreApplication.translate("VulkanModelDownloadWorker", "Downloading Vulkan model…"))
        install_ggml_model(asr_model, asr_models_root, progress=worker._on_progress, cancel_event=worker.cancel_event)
        if not worker.check_cancelled():
            install_vad_model(asr_models_root, progress=worker._on_progress, cancel_event=worker.cancel_event)
        return QCoreApplication.translate("VulkanModelDownloadWorker", "Vulkan model installed successfully.")

    return _task

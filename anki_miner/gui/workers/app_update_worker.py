"""Worker thread for the in-place app update (services/app_updater.py)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.exceptions import OperationCancelled
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.app_updater import stage_update
from anki_miner.services.update_checker import UpdateInfo

logger = logging.getLogger(__name__)


class AppUpdateWorker(CancellableWorker):
    """Download, verify and stage one update off the GUI thread.

    ``progress(downloaded, total)`` carries bytes (``total`` is 0 when the
    server sends no length; app_updater.MAX_UPDATE_BYTES keeps both under
    2**31). ``result_ready`` carries the :class:`StagedUpdate`, or the
    exception on failure. A cancelled run emits nothing, and the native
    ``finished`` is then the only word, which is how the controller tells a
    cancel apart from a result.

    A finished stage is always reported, even when a cancel landed after it:
    an AppImage replaced on disk is installed, and calling it cancelled would
    hide that.
    """

    progress = pyqtSignal(int, int)
    result_ready = pyqtSignal(object)

    def __init__(self, info: UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self._info = info

    def run(self) -> None:
        self.log_start("AppUpdateWorker", version=self._info.version, target=self._info.target)
        if self.check_cancelled():
            return
        try:
            staged = stage_update(self._info, progress=self._on_progress, cancelled_check=self.check_cancelled)
        except OperationCancelled:
            logger.info("App update cancelled")
            return
        except Exception as exc:  # noqa: BLE001 — every failure goes back to the banner
            # Rebound: Python unbinds `exc` when the except block ends.
            failure = exc

            def _emit_failure(_msg: str) -> None:
                self.result_ready.emit(failure)

            self.report_failure(
                exc,
                context="AppUpdateWorker",
                on_error=_emit_failure,
                cancel_flag_suppresses_error=False,
            )
            return
        self.result_ready.emit(staged)

    def _on_progress(self, downloaded: int, total: int, _message: str) -> None:
        self.progress.emit(downloaded, total)

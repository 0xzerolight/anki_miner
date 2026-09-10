"""Worker for Utilities → Manga OCR: sequential mokuro runs, one per volume.

One :class:`~anki_miner.services.mokuro_runner.MokuroRunnerService` call per
volume on the shared :class:`FileQueueWorker` 5-signal contract. A volume whose
``.mokuro`` already exists is ``file_skipped`` unless the run asked to redo it;
a missing mokuro executable dooms every remaining volume, so it stops the
whole queue (``is_cancelled`` stays False — a tool error, not a user cancel).
"""

from __future__ import annotations

import logging

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.base import AnkiMinerException
from anki_miner.exceptions.mokuro import MokuroNotFoundError
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.mokuro_runner import MokuroOptions, MokuroRunnerService, MokuroStatus
from anki_miner.services.mokuro_volumes import MokuroVolume

logger = logging.getLogger(__name__)


class MokuroWorker(FileQueueWorker):
    """OCR each queued volume in place via mokuro."""

    _FATAL_QUEUE_EXCEPTIONS = (MokuroNotFoundError,)

    def __init__(
        self,
        config: AnkiMinerConfig,
        volumes: list[MokuroVolume],
        *,
        options: MokuroOptions,
        skip_processed: bool = True,
        service: MokuroRunnerService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._volumes = list(volumes)
        self._options = options
        self._skip_processed = skip_processed
        self._service = MokuroRunnerService(config) if service is None else service

    def _queue_items(self) -> list[MokuroVolume]:
        return self._volumes

    def _process_item(self, idx: int, volume: MokuroVolume) -> None:
        # Live check, not the scan-time snapshot: the user may have deleted the
        # sidecar between picking the folder and pressing Run.
        if self._skip_processed and volume.output.is_file():
            reason = self.tr("Already processed — tick Redo to run OCR again")
            self.file_progress.emit(idx, 100, reason)
            self.file_skipped.emit(idx, volume.output, reason)
            return

        def _progress(message: str, frac: float | None) -> None:
            if frac is not None:
                # The message already carries the real count ("Page 3 of 40"),
                # which IS the percentage — D18 asks for the count, not both.
                self.file_progress.emit(idx, int(frac * 100), message)
            else:
                self.file_progress.emit(idx, 0, message)

        try:
            result = self._service.process_volume(
                volume, self._options, progress_cb=_progress, cancel_event=self._cancel_event
            )
        except MokuroNotFoundError:
            raise  # base loop's fatal-queue stop
        except AnkiMinerException as exc:
            logger.warning("mokuro_worker: %s failed: %s", volume.source.name, exc)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
            return

        if result.status is MokuroStatus.DONE:
            self.file_progress.emit(idx, 100, self.tr("Done"))
            self.file_finished.emit(idx, result.output_path, None)
        elif result.status is MokuroStatus.CANCELLED:
            self.file_finished.emit(idx, None, self.tr("Cancelled"))
        else:  # pragma: no cover - exhaustiveness guard
            raise ValueError(f"Unsupported mokuro status: {result.status!r}")

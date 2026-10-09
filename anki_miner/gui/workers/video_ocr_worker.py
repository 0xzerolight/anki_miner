"""Queue worker for Utilities → Video OCR: one video per item, one <stem>.srt out."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.region import Region
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.services.video_ocr.scanner import VideoOcrResult

logger = logging.getLogger(__name__)


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class VideoOcrWorker(FileQueueWorker):
    """Scan each video's region into ``<stem>.srt`` (next to it, or in ``output_dir``)."""

    #: A runtime or model that cannot load fails every file the same way: stop the queue.
    _FATAL_QUEUE_EXCEPTIONS = (EngineLoadError,)

    def __init__(
        self,
        config,
        video_files: list[Path],
        region: Region,
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._video_files = list(video_files)
        self._region = region
        self._output_dir = output_dir
        self._overwrite = overwrite

    def _queue_items(self) -> list[Path]:
        return self._video_files

    def _process_item(self, idx: int, video: Path) -> None:
        # The scan stack (numpy) loads with the first run, never at app startup.
        from anki_miner.services.video_ocr import scanner

        out_dir = self._output_dir if self._output_dir is not None else video.parent
        out_srt = resolve_output_path(out_dir, video.stem + ".srt")
        if out_srt.exists() and not self._overwrite:
            message = self.tr("Skipped, exists")
            self.file_progress.emit(idx, 100, message)
            self.file_skipped.emit(idx, out_srt, message)
            return
        last_second = -1

        def _progress(t: float, duration: float | None) -> None:
            nonlocal last_second
            if int(t) == last_second:
                return
            last_second = int(t)
            if duration:
                pct = min(99, int(t * 100 / duration))
                message = tr_format(self.tr("Reading subtitles: %1 / %2"), _clock(t), _clock(duration))
            else:
                pct = 0
                message = tr_format(self.tr("Reading subtitles: %1"), _clock(t))
            self.file_progress.emit(idx, pct, message)

        result = scanner.scan_video(
            self._config, video, self._region, out_srt, progress_cb=_progress, cancel_event=self._cancel_event
        )
        self._emit_result(idx, video, result)

    def _emit_result(self, idx: int, video: Path, result: VideoOcrResult) -> None:
        from anki_miner.services.video_ocr.scanner import VideoOcrStatus

        status = result.status
        if status is VideoOcrStatus.SUCCESS:
            self.file_progress.emit(idx, 100, self.tr("Done"))
            self.file_finished.emit(idx, result.out_srt, None)
        elif status is VideoOcrStatus.NO_TEXT:
            self.file_skipped.emit(idx, video, self.tr("No subtitles found in the region"))
        elif status is VideoOcrStatus.DECODE_FAILED:
            logger.warning("video_ocr_worker: %s: %s", video, result.detail)
            self.file_finished.emit(idx, None, tr_format(self.tr("Could not read the video %1"), video.name))
        # CANCELLED: a run-level outcome; a per-item error would raise the problem banner.

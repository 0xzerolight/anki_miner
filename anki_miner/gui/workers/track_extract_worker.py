"""Worker for Utilities → Tracks: save the ticked tracks of each video.

Each video is probed again (a folder's episodes need not share a layout), its
ticked positions are matched, and every track is one ffmpeg copy through
:class:`~anki_miner.services.track_extractor.TrackExtractorService`. Every
track gets its own ``file_note`` line, so a run over a season says exactly
which episode lacked which track.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.track_extractor import (
    ExtractStatus,
    PlannedTrack,
    TrackExtractorService,
    TrackRef,
    plan_outputs,
)
from anki_miner.utils.i18n import tr_format


class TrackExtractWorker(FileQueueWorker):
    """Save each video's ticked tracks; one ``file_note`` per track."""

    #: (idx, message): one line per track, saved, skipped or missing.
    file_note = pyqtSignal(int, str)
    #: ffmpeg cannot start: every remaining video would fail the same way.
    _FATAL_QUEUE_EXCEPTIONS = (FfmpegNotFoundError,)

    def __init__(
        self,
        config: AnkiMinerConfig,
        videos: Sequence[Path],
        ticked: Sequence[TrackRef],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        service: TrackExtractorService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._videos = list(videos)
        self._ticked = tuple(ticked)
        self._output_dir = output_dir
        self._overwrite = overwrite
        self._service = service or TrackExtractorService(config)
        #: Destinations this run wrote: EP01.mkv and EP01.mp4 in one folder must not overwrite each other.
        self._claimed: set[Path] = set()

    def _queue_items(self) -> list[Path]:
        return self._videos

    def _label(self, ref: TrackRef) -> str:
        if ref.kind == "subtitle":
            return tr_format(self.tr("Subtitle %1"), ref.position + 1)
        return tr_format(self.tr("Audio %1"), ref.position + 1)

    def _process_item(self, idx: int, video: Path) -> None:
        out_dir = self._output_dir or video.parent
        planned, missing = plan_outputs(video, self._service.probe(video), self._ticked, out_dir)
        for ref in missing:
            self.file_note.emit(idx, tr_format(self.tr("%1 has no %2 — skipped"), video.name, self._label(ref)))
        saved = 0
        failures: list[str] = []
        for n, plan in enumerate(planned):
            if self.is_cancelled:
                return
            reason = self._skip_reason(video, plan)
            if reason is not None:
                self.file_note.emit(idx, reason)
                continue
            self.file_progress.emit(idx, n * 100 // len(planned), tr_format(self.tr("Saving %1"), plan.dest.name))
            result = self._service.extract(video, plan, cancel_event=self._cancel_event)
            if result.status is ExtractStatus.CANCELLED:
                return
            if result.status is ExtractStatus.FAILED:
                failures.append(
                    tr_format(
                        self.tr("%1: %2 could not be saved: %3"), video.name, self._label(plan.ref), result.reason
                    )
                )
                continue
            self._claimed.add(plan.dest.resolve())
            saved += 1
            self.file_note.emit(idx, tr_format(self.tr("Saved %1"), plan.dest.name))
        if failures:
            self.file_finished.emit(idx, None, "\n".join(failures))
        elif saved:
            self.file_finished.emit(idx, video, None)
        else:
            self.file_skipped.emit(idx, video, self.tr("No ticked track was saved"))

    def _skip_reason(self, video: Path, plan: PlannedTrack) -> str | None:
        """Why this track must not be written, or None to write it."""
        dest = plan.dest
        if not dest.exists():
            return None
        try:
            same_as_source = dest.samefile(video)
        except OSError:
            same_as_source = False
        if same_as_source:
            return tr_format(self.tr("%1 would replace the video itself — skipped"), dest.name)
        if dest.resolve() in self._claimed:
            return tr_format(self.tr("%1 was already saved from another video in this run — skipped"), dest.name)
        if not self._overwrite:
            return tr_format(self.tr("%1 already exists — tick Overwrite to replace it"), dest.name)
        return None

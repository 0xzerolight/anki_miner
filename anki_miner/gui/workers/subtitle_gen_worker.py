"""Worker that transcribes video files to SRT subtitles using the ASR engine.

The 5-signal contract and per-file queue loop live in
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; the per-file
transcription pipeline (temp-WAV lifecycle, extract → load → transcribe → write,
and the "no speech" decision) lives in
:func:`~anki_miner.services.asr.subtitle_generation.generate_subtitle_one`. This
worker is the signal adapter: it resolves the output path, runs the skip gate,
calls the service, and maps its structured
:class:`~anki_miner.services.asr.subtitle_generation.SubtitleGenStatus` back to
translated messages.
"""

from __future__ import annotations

import logging
from pathlib import Path

from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.asr.subtitle_generation import (
    SubtitleGenResult,
    SubtitleGenStatus,
    generate_subtitle_one,
)
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class SubtitleGenWorker(FileQueueWorker):
    """Transcribe a list of video files to SRT subtitle files.

    Per file:
    1. Emits ``file_started(idx)``.
    2. If ``<stem>.srt`` already exists and *overwrite* is False — emits
       ``file_skipped(idx, existing_path)`` and continues.
    3. Delegates to
       :func:`~anki_miner.services.asr.subtitle_generation.generate_subtitle_one`,
       which extracts audio, transcribes, and writes the SRT (progress forwarded
       via ``file_progress``).
    4. Maps the returned
       :class:`~anki_miner.services.asr.subtitle_generation.SubtitleGenStatus` to a
       translated ``file_finished`` message.

    Cancel is honored between files and propagated into the extractor /
    transcriber via ``self._cancel_event``.

    After the loop, ``queue_finished()`` is emitted unconditionally.

    Args:
        config: Frozen :class:`~anki_miner.config.AnkiMinerConfig` instance.
        video_files: Ordered list of paths to video files to transcribe.
        output_dir: When given, SRT files are written here instead of next to
            each source video.
        overwrite: When ``True``, existing SRT files are re-generated.
        extractor: Optional :class:`~anki_miner.services.media_extractor.MediaExtractorService`
            instance; one is created from *config* if omitted.
        parent: Optional parent QObject.
    """

    def __init__(
        self,
        config,
        video_files: list[Path],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        extractor=None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._video_files = list(video_files)
        self._output_dir = output_dir
        self._overwrite = overwrite

        if extractor is None:
            from anki_miner.services.media_extractor import MediaExtractorService

            self._extractor = MediaExtractorService(config)
        else:
            self._extractor = extractor

    def _queue_items(self) -> list[Path]:
        return self._video_files

    def _process_item(self, idx: int, video_path: Path) -> None:
        # Determine output SRT path, resolved against existing on-disk files
        # so a re-generate overwrites a visually-identical (NFC/NFD- or
        # case-variant) subtitle in place instead of spawning a Windows
        # duplicate. See resolve_output_path.
        out_dir = self._output_dir if self._output_dir is not None else video_path.parent
        out_srt = resolve_output_path(out_dir, video_path.stem + ".srt")

        # Skip-if-exists logic.
        if out_srt.exists() and not self._overwrite:
            logger.debug("subtitle_gen_worker: skipped %s (exists)", out_srt)
            self.file_progress.emit(idx, 100, self.tr("Skipped, exists"))
            self.file_skipped.emit(idx, out_srt)
            return

        self._process_file(idx, video_path, out_srt)

    def _process_file(self, idx: int, video_path: Path, out_srt: Path) -> None:
        """Run :func:`generate_subtitle_one` for one file; never raises (errors forwarded)."""

        def _on_extract_start() -> None:
            self.file_progress.emit(idx, 0, tr_format(self.tr("Extracting audio: %1"), video_path.name))

        def _transcribe_progress(fraction: float) -> None:
            pct = min(int(fraction * 100), 100)
            self.file_progress.emit(idx, pct, tr_format(self.tr("Transcribing: %1%"), pct))

        try:
            result = generate_subtitle_one(
                self._config,
                self._extractor,
                video_path,
                out_srt,
                on_extract_start=_on_extract_start,
                transcribe_progress_cb=_transcribe_progress,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            logger.exception("subtitle_gen_worker: error on %s", video_path)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
            return

        self._emit_result(idx, video_path, result)

    def _emit_result(self, idx: int, video_path: Path, result: SubtitleGenResult) -> None:
        """Map a :class:`SubtitleGenResult` status code to translated worker signals."""
        name = video_path.name
        status = result.status

        if status is SubtitleGenStatus.SUCCESS:
            # Force 100% on success.
            self.file_progress.emit(idx, 100, self.tr("Done"))
            self.file_finished.emit(idx, result.out_srt, None)
        elif status is SubtitleGenStatus.CANCELLED:
            self.file_finished.emit(idx, None, self.tr("Cancelled"))
        elif status is SubtitleGenStatus.NO_SPEECH:
            self.file_finished.emit(idx, None, tr_format(self.tr("No speech detected in %1"), name))
        elif status is SubtitleGenStatus.EXTRACTION_FAILED:
            self.file_finished.emit(idx, None, tr_format(self.tr("Audio extraction failed for %1"), name))

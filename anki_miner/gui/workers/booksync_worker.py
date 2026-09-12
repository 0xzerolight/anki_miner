"""Worker that syncs one book to a queue of audio files (Utilities → Audiobook Sync).

Signal adapter over :mod:`anki_miner.services.book_sync.pipeline`, the way
:class:`~anki_miner.gui.workers.subtitle_gen_worker.SubtitleGenWorker` sits over
``generate_subtitle_one``: the 5-signal queue contract comes from
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; this class
resolves the output path, runs the skip gate, calls the service and maps its
:class:`~anki_miner.services.book_sync.pipeline.BookSyncStatus` to translated
messages.

The book is loaded once, on this thread, before the first file; a failure
there propagates to ``FileQueueWorker.run``, which reports it through
``error`` and finishes the queue as FAILED — every file depends on it. The
:class:`BookCursor` is shared across the files, so a folder of per-chapter
files is one continuous read of the book.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.gui.workers.reading_queue_worker import reading_decode_ladder
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.book_sync.aligner import BookCursor, BookText
from anki_miner.services.book_sync.pipeline import BookSyncResult, BookSyncStatus, load_book, sync_one
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.services.asr.transcriber import Ct2ModelSession

logger = logging.getLogger(__name__)


class BookSyncWorker(FileQueueWorker):
    """Sync ``book_path`` to each of ``audio_files``, writing ``<stem>.srt`` per file."""

    def __init__(
        self,
        config,
        audio_files: list[Path],
        book_path: Path,
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        extractor=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._audio_files = list(audio_files)
        self._book_path = book_path
        self._output_dir = output_dir
        self._overwrite = overwrite
        if extractor is None:
            from anki_miner.services.media_extractor import MediaExtractorService

            self._extractor = MediaExtractorService(config)
        else:
            self._extractor = extractor
        self._ct2_model_session: Ct2ModelSession | None = None
        self._book: BookText | None = None
        self._cursor = BookCursor()

    def _process_queue(self) -> None:
        from anki_miner.services.asr.transcriber import Ct2ModelSession

        profile = get_profile(config_language(self._config))
        self._book = load_book(
            self._book_path,
            rules=profile.sentence_rules,
            encodings=reading_decode_ladder(self._config),
            cancel_check=lambda: self.is_cancelled,
        )
        self._cursor = BookCursor()
        session = Ct2ModelSession()
        self._ct2_model_session = session
        try:
            super()._process_queue()
        finally:
            self._ct2_model_session = None
            session.release()

    def _queue_items(self) -> list[Path]:
        return self._audio_files

    def _process_item(self, idx: int, audio_path: Path) -> None:
        out_dir = self._output_dir if self._output_dir is not None else audio_path.parent
        out_srt = resolve_output_path(out_dir, audio_path.stem + ".srt")
        if out_srt.exists() and not self._overwrite:
            logger.debug("booksync_worker: skipped %s (exists)", out_srt)
            msg = self.tr("Skipped, exists")
            self.file_progress.emit(idx, 100, msg)
            self.file_skipped.emit(idx, out_srt, msg)
            return
        self._process_file(idx, audio_path, out_srt)

    def _process_file(self, idx: int, audio_path: Path, out_srt: Path) -> None:
        assert self._book is not None  # loaded in _process_queue

        def _on_extract_start() -> None:
            self.file_progress.emit(idx, 0, tr_format(self.tr("Extracting audio: %1"), audio_path.name))

        def _on_transcribe_start() -> None:
            self.file_progress.emit(idx, 0, tr_format(self.tr("Transcribing: %1%"), 0))

        def _transcribe_progress(fraction: float) -> None:
            pct = min(int(fraction * 100), 100)
            self.file_progress.emit(idx, pct, tr_format(self.tr("Transcribing: %1%"), pct))

        def _on_align_start() -> None:
            self.file_progress.emit(idx, 100, self.tr("Aligning to the book…"))

        def _log(line: str) -> None:
            # The aligner's re-anchor / skip notes: diagnostics in the
            # service's own words, surfaced like Generate's service lines.
            self.file_progress.emit(idx, 100, line)

        try:
            result = sync_one(
                self._config,
                self._extractor,
                audio_path,
                self._book,
                self._cursor,
                out_srt,
                on_extract_start=_on_extract_start,
                on_transcribe_start=_on_transcribe_start,
                on_align_start=_on_align_start,
                transcribe_progress_cb=_transcribe_progress,
                cancel_event=self._cancel_event,
                ct2_model_session=self._ct2_model_session,
                language=get_profile(config_language(self._config)).asr_language,
                log=_log,
            )
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            logger.exception("booksync_worker: error on %s", audio_path)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
            return
        self._emit_result(idx, audio_path, result)

    def _emit_result(self, idx: int, audio_path: Path, result: BookSyncResult) -> None:
        name = audio_path.name
        status = result.status
        if status is BookSyncStatus.SUCCESS:
            self.file_progress.emit(
                idx,
                100,
                tr_format(
                    self.tr("%1 sentences timed, %2 in between had no audio"),
                    result.cues,
                    result.unmatched_sentences,
                ),
            )
            self.file_finished.emit(idx, result.out_srt, None)
        elif status is BookSyncStatus.CANCELLED:
            pass  # run-level outcome; the tab's status label already says Cancelled
        elif status is BookSyncStatus.NO_SPEECH:
            self.file_skipped.emit(idx, audio_path, self.tr("No speech detected"))
        elif status is BookSyncStatus.NO_MATCH:
            self.file_finished.emit(idx, None, tr_format(self.tr("Nothing in %1 matched the book"), name))
        elif status is BookSyncStatus.EXTRACTION_FAILED:
            self.file_finished.emit(idx, None, tr_format(self.tr("Audio extraction failed for %1"), name))

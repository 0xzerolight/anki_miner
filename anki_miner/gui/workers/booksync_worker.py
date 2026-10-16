"""Worker that syncs books to their audio files (Utilities → Audiobook Sync).

Signal adapter over :mod:`anki_miner.services.book_sync.pipeline`, the way
:class:`~anki_miner.gui.workers.subtitle_gen_worker.SubtitleGenWorker` sits over
``generate_subtitle_one``: the 5-signal queue contract comes from
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; this class
resolves the output path, runs the skip gate, calls the service and maps its
:class:`~anki_miner.services.book_sync.pipeline.BookSyncStatus` to translated
messages.

A run is one or more books (WP-27: a shelf folder queues one book per
subfolder), each read by its audio files in order. The queue items are those
files, book after book. Each book is loaded once, before its first file, and
gets its own :class:`BookCursor`, so a folder of per-chapter files is one
continuous read of the book. A one-book run loads its book before the first
file and a failure there propagates to ``FileQueueWorker.run`` (every file
depends on it); on a shelf a book that will not load fails its first file and
skips the rest of its files, and the next book still runs. When a book's
files are done, :attr:`book_report` carries its sync report (MS-25).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.utils.service_factory import import_decode_ladder
from anki_miner.gui.workers.file_queue_worker import UNMEASURED, FileQueueWorker
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.book_sync.aligner import BookCursor, BookText
from anki_miner.services.book_sync.library import BookJob
from anki_miner.services.book_sync.pipeline import BookSyncResult, BookSyncStatus, load_book, sync_one
from anki_miner.services.book_sync.report import build_report
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.services.asr.transcriber import Ct2ModelSession

logger = logging.getLogger(__name__)


class BookSyncWorker(FileQueueWorker):
    """Sync each book to its audio files, writing ``<stem>.srt`` per file.

    Args:
        config: Frozen application configuration.
        audio_files: The one book's audio files, in reading order (ignored when ``books`` is given).
        book_path: The one book (ignored when ``books`` is given).
        books: A shelf run: one :class:`BookJob` per book, run in order.
        output_dir: Where the ``.srt`` files go; None writes each beside its audio.
        overwrite: Replace an existing ``.srt`` instead of skipping its audio file.
        extractor: Media extractor (a ``MediaExtractorService`` by default).
        parent: Optional QObject parent.
    """

    #: A finished book's :class:`~anki_miner.services.book_sync.report.BookSyncReport`.
    book_report = pyqtSignal(object)

    def __init__(
        self,
        config,
        audio_files: list[Path],
        book_path: Path | None,
        *,
        books: Sequence[BookJob] = (),
        output_dir: Path | None = None,
        overwrite: bool = False,
        extractor=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        if books:
            self._jobs = list(books)
        else:
            assert book_path is not None, "a one-book run names its book"
            self._jobs = [BookJob(book=book_path, audio_files=tuple(audio_files))]
        self._output_dir = output_dir
        self._overwrite = overwrite
        if extractor is None:
            from anki_miner.services.media_extractor import MediaExtractorService

            self._extractor = MediaExtractorService(config)
        else:
            self._extractor = extractor
        self._ct2_model_session: Ct2ModelSession | None = None
        self._items: list[tuple[int, Path]] = [
            (job_idx, audio) for job_idx, job in enumerate(self._jobs) for audio in job.audio_files
        ]
        self._job_idx: int | None = None  # the book whose files are running
        self._book: BookText | None = None
        self._book_error: str | None = None  # why the running book did not load (shelf runs)
        self._cursor = BookCursor()
        self._speech_seconds = 0.0
        self._matched_seconds = 0.0

    def _process_queue(self) -> None:
        from anki_miner.services.asr.transcriber import Ct2ModelSession

        if len(self._jobs) == 1:
            # One book: every file depends on it, so a load failure fails the run.
            self._start_book(0, raise_on_failure=True)
        session = Ct2ModelSession()
        self._ct2_model_session = session
        try:
            super()._process_queue()
            if not self.is_cancelled:
                self._finish_book()
        finally:
            self._ct2_model_session = None
            session.release()

    def _start_book(self, job_idx: int, *, raise_on_failure: bool = False) -> None:
        """Make ``job_idx`` the running book: load it and give it a fresh cursor."""
        self._job_idx = job_idx
        self._book = None
        self._book_error = None
        self._cursor = BookCursor()
        self._speech_seconds = 0.0
        self._matched_seconds = 0.0
        profile = get_profile(config_language(self._config))
        try:
            self._book = load_book(
                self._jobs[job_idx].book,
                rules=profile.sentence_rules,
                encodings=import_decode_ladder(self._config),
                cancel_check=lambda: self.is_cancelled,
            )
        except Exception as exc:  # noqa: BLE001 — a shelf moves on to its next book; one book re-raises
            if raise_on_failure:
                raise
            logger.warning(
                "booksync_worker: could not load %s: %s: %s", self._jobs[job_idx].book, type(exc).__name__, exc
            )
            self._book_error = str(exc)

    def _finish_book(self) -> None:
        """Report the running book, if any of its files timed a sentence."""
        if self._job_idx is None or self._book is None:
            return
        report = build_report(
            self._jobs[self._job_idx].book.name, self._cursor, self._speech_seconds, self._matched_seconds
        )
        if report is not None:
            self.book_report.emit(report)

    def _queue_items(self) -> list[tuple[int, Path]]:
        return self._items

    def _process_item(self, idx: int, item: tuple[int, Path]) -> None:
        job_idx, audio_path = item
        job = self._jobs[job_idx]
        if job_idx != self._job_idx:
            self._finish_book()
            self._start_book(job_idx)
            if len(self._jobs) > 1:
                self.file_progress.emit(idx, UNMEASURED, tr_format(self.tr("Book: %1"), job.book.name))
            if self._book_error is not None:
                self.file_finished.emit(
                    idx, None, tr_format(self.tr("%1 could not be loaded: %2"), job.book.name, self._book_error)
                )
                return
        if self._book is None:
            self.file_skipped.emit(idx, audio_path, self.tr("Its book could not be loaded"))
            return
        out_dir = self._output_dir if self._output_dir is not None else audio_path.parent
        if self._output_dir is not None and job.out_subdir is not None:
            out_dir = self._output_dir / job.out_subdir
        out_srt = resolve_output_path(out_dir, audio_path.stem + ".srt")
        if out_srt.exists() and not self._overwrite:
            logger.debug("booksync_worker: skipped %s (exists)", out_srt)
            msg = self.tr("Skipped, exists")
            self.file_progress.emit(idx, 100, msg)
            self.file_skipped.emit(idx, out_srt, msg)
            return
        self._process_file(idx, audio_path, out_srt)

    def _process_file(self, idx: int, audio_path: Path, out_srt: Path) -> None:
        assert self._book is not None  # loaded by _start_book

        def _on_extract_start() -> None:
            self.file_progress.emit(idx, UNMEASURED, tr_format(self.tr("Extracting audio: %1"), audio_path.name))

        def _on_transcribe_start() -> None:
            self.file_progress.emit(idx, 0, tr_format(self.tr("Transcribing: %1%"), 0))

        def _transcribe_progress(fraction: float) -> None:
            pct = min(int(fraction * 100), 100)
            self.file_progress.emit(idx, pct, tr_format(self.tr("Transcribing: %1%"), pct))

        def _on_align_start() -> None:
            self.file_progress.emit(idx, UNMEASURED, self.tr("Aligning to the book…"))

        def _on_transcript_reused() -> None:
            # F-12: hours of transcription skipped; say so instead of a silent jump to aligning.
            self.file_progress.emit(idx, UNMEASURED, self.tr("Reusing the saved transcript"))

        def _on_out_of_order(sentence: int) -> None:
            # MS-24: an earlier passage heard again later in this file.
            self.file_progress.emit(
                idx,
                UNMEASURED,
                tr_format(self.tr("Found sentence %1 again later in the audio; timed it out of book order"), sentence),
            )

        def _log(line: str) -> None:
            # The aligner's re-anchor / skip notes: diagnostics in the
            # service's own words, surfaced like Generate's service lines.
            self.file_progress.emit(idx, UNMEASURED, line)

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
                on_transcript_reused=_on_transcript_reused,
                on_out_of_order=_on_out_of_order,
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
            self._speech_seconds += result.speech_seconds
            self._matched_seconds += result.matched_seconds
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

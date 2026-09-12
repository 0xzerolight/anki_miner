"""Per-audio-file sync policy for Utilities → Audiobook Sync.

Mirrors :mod:`anki_miner.services.asr.subtitle_generation`: a structured
status the worker maps to translated messages, no Qt, no i18n. The book is
loaded ONCE per queue (:func:`load_book`) and threaded through every file
with a shared :class:`BookCursor`, so a folder of per-chapter files reads as
one book.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anki_miner.exceptions import SetupError
from anki_miner.services.book_sync.aligner import BookCursor, BookText, TimedText, align_to_book
from anki_miner.services.book_sync.normalize import normalize_for_alignment
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import SentenceRules
    from anki_miner.services.asr.transcriber import Ct2ModelSession

logger = logging.getLogger(__name__)

_BOOK_KINDS = frozenset({"epub", "txt"})


class BookSyncStatus(Enum):
    SUCCESS = auto()
    #: ``transcribe_media`` could not extract the audio.
    EXTRACTION_FAILED = auto()
    #: The transcript had no writable segment (silence / music-only).
    NO_SPEECH = auto()
    #: There was speech, but none of it aligned to any sentence of the book.
    NO_MATCH = auto()
    #: A cancel landed during transcription or alignment.
    CANCELLED = auto()


@dataclass(frozen=True)
class BookSyncResult:
    status: BookSyncStatus
    out_srt: Path | None = None
    #: Sentences written as cues.
    cues: int = 0
    #: Sentences between the first and last cue that got no audio (skipped by
    #: the narrator, or lost to a transcription hole).
    unmatched_sentences: int = 0


def load_book(
    path: Path,
    *,
    rules: SentenceRules | None,
    encodings: tuple[str, ...] | None,
    cancel_check: Callable[[], bool] | None = None,
) -> BookText:
    """Load an ``.epub``/``.txt`` through the reading detector into a :class:`BookText`.

    Raises :class:`SetupError` for anything that is not a book, for a DRM or
    structurally broken EPUB (the loader's own error), and for a book with no
    text. ``rules``/``encodings`` are the mining language's sentence policy
    and decode ladder, exactly as ``reading_queue_worker`` passes them.
    """
    from anki_miner.services.reading import detector

    ref = detector.detect(path)[0]
    if ref.kind not in _BOOK_KINDS:
        raise SetupError(f"'{path.name}' is not a book (.epub or .txt).")
    kwargs: dict[str, Any] = {}
    if cancel_check is not None:
        kwargs["cancel_check"] = cancel_check
    if encodings is not None:
        kwargs["encodings"] = encodings
    if rules is not None:
        kwargs["rules"] = rules
    doc = detector.load(ref, **kwargs)
    sentences = tuple(unit.text for unit in doc.units)
    if not sentences:
        raise SetupError(f"'{path.name}' contains no text to sync.")
    book = BookText(
        title=doc.title,
        sentences=sentences,
        keys=tuple(normalize_for_alignment(text) for text in sentences),
    )
    log_summary(logger, "Book loaded for sync", file=path, sentences=len(sentences), chars=sum(map(len, book.keys)))
    return book


def sync_one(
    config: AnkiMinerConfig,
    extractor: Any,
    audio_path: Path,
    book: BookText,
    cursor: BookCursor,
    out_srt: Path,
    *,
    on_extract_start: Callable[[], None] | None = None,
    on_transcribe_start: Callable[[], None] | None = None,
    on_align_start: Callable[[], None] | None = None,
    transcribe_progress_cb: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    ct2_model_session: Ct2ModelSession | None = None,
    language: str = "ja",
    log: Callable[[str], None] | None = None,
) -> BookSyncResult:
    """Transcribe *audio_path*, align it to *book* from *cursor*, write *out_srt*.

    ``on_align_start`` fires after transcription, before alignment (the
    worker's "Aligning to the book…" line). ``log`` receives the aligner's
    re-anchor / skip notes, in the app's own words for the Activity log.
    """
    from anki_miner.services.asr import long_audio, srt_writer

    transcript = long_audio.transcribe_media(
        config,
        extractor,
        audio_path,
        cancel_event=cancel_event,
        progress_cb=transcribe_progress_cb,
        on_extract_start=on_extract_start,
        on_transcribe_start=on_transcribe_start,
        ct2_model_session=ct2_model_session,
        language=language,
    )
    if transcript.status is long_audio.LongAudioStatus.CANCELLED:
        return BookSyncResult(BookSyncStatus.CANCELLED)
    if transcript.status is long_audio.LongAudioStatus.EXTRACTION_FAILED:
        return BookSyncResult(BookSyncStatus.EXTRACTION_FAILED)
    if not srt_writer.writable_segments(transcript.segments):
        logger.info("book_sync: no speech detected in %s", audio_path)
        return BookSyncResult(BookSyncStatus.NO_SPEECH)

    if on_align_start is not None:
        on_align_start()
    timings = align_to_book(
        [TimedText(start, end, text) for start, end, text in transcript.segments],
        book,
        cursor,
        cancel_check=(lambda: cancel_event.is_set()) if cancel_event is not None else None,
        log=log,
    )
    if cancel_event is not None and cancel_event.is_set():
        return BookSyncResult(BookSyncStatus.CANCELLED)
    if not timings:
        logger.info("book_sync: nothing in %s matched the book", audio_path)
        return BookSyncResult(BookSyncStatus.NO_MATCH)

    out_srt.parent.mkdir(parents=True, exist_ok=True)
    srt_writer.segments_to_srt([(t.start, t.end, book.sentences[t.index]) for t in timings], out_srt)
    span = timings[-1].index - timings[0].index + 1
    unmatched = span - len(timings)
    log_summary(
        logger,
        "Book sync done",
        file=audio_path,
        cues=len(timings),
        unmatched=unmatched,
        first=timings[0].index,
        last=timings[-1].index,
    )
    return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out_srt, cues=len(timings), unmatched_sentences=unmatched)

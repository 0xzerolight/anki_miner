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
from anki_miner.services.book_sync.aligner import BookCursor, BookText, SentenceTiming, TimedText, align_to_book
from anki_miner.services.book_sync.normalize import normalize_for_alignment
from anki_miner.services.book_sync.report import UnheardGap, speech_coverage, unheard_gaps
from anki_miner.utils.logging_ext import capped, log_summary

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
    #: Seconds of transcript speech, and how many of them a cue covers (MS-25).
    speech_seconds: float = 0.0
    matched_seconds: float = 0.0
    #: One per cue, in the SRT's order (audio order): the book sentence it times, and when.
    timings: tuple[SentenceTiming, ...] = ()
    #: The unheard runs between this file's cues (MS-24); what an earlier file timed is not in them.
    unheard: tuple[UnheardGap, ...] = ()


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
    on_transcript_reused: Callable[[], None] | None = None,
    on_out_of_order: Callable[[int], None] | None = None,
) -> BookSyncResult:
    """Transcribe *audio_path*, align it to *book* from *cursor*, write *out_srt*.

    ``on_align_start`` fires after transcription, before alignment (the
    worker's "Aligning to the book…" line). ``log`` receives the aligner's
    re-anchor / skip notes, in the app's own words for the Activity log.
    The transcript is read from, and saved to, the transcript cache shared
    with Generate (F-12); ``on_transcript_reused`` fires when it came from
    there. ``on_out_of_order`` gets the sentence number where a passage read
    out of book order starts (MS-24). Segments are re-broken at the mining language's sentence ends
    (MS-21), the same transcript Generate writes, which also keeps the
    aligner's per-segment time interpolation short.
    """
    from anki_miner.languages.registry import config_language, get_profile
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
        sentence_rules=get_profile(config_language(config)).sentence_rules,
        use_cache=True,
        on_cache_hit=on_transcript_reused,
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
    # What earlier files of this book timed (MS-24): a detour spans text they
    # already synced, and that text is not "unheard" here.
    already = set(cursor.timed)
    timings = align_to_book(
        [TimedText(start, end, text) for start, end, text in transcript.segments],
        book,
        cursor,
        cancel_check=(lambda: cancel_event.is_set()) if cancel_event is not None else None,
        log=log,
        on_out_of_order=on_out_of_order,
    )
    if cancel_event is not None and cancel_event.is_set():
        return BookSyncResult(BookSyncStatus.CANCELLED)
    if not timings:
        logger.info("book_sync: nothing in %s matched the book", audio_path)
        return BookSyncResult(BookSyncStatus.NO_MATCH)

    out_srt.parent.mkdir(parents=True, exist_ok=True)
    # Timings are in audio order (a detour's cues sit where the audio has them, MS-24).
    srt_writer.segments_to_srt([(t.start, t.end, book.sentences[t.index]) for t in timings], out_srt)
    first = min(t.index for t in timings)
    last = max(t.index for t in timings)
    timed_here = {t.index for t in timings}
    unmatched = sum(1 for i in range(first, last + 1) if i not in timed_here and i not in already)
    # ``already`` (B4): what earlier files of this book timed is not unheard here.
    gaps = unheard_gaps(timings, known=already)
    if gaps:
        # Never a cue (a guessed time would put the wrong audio on a card);
        # the guess goes to the app log so the user can find the passage.
        log_summary(
            logger,
            "Book sync unheard",
            file=audio_path,
            gaps=capped(f"{g.first + 1}-{g.last + 1}@{g.start:.1f}-{g.end:.1f}s" for g in gaps),
        )
    speech_seconds, matched_seconds = speech_coverage(transcript.segments, timings)
    log_summary(
        logger,
        "Book sync done",
        file=audio_path,
        cues=len(timings),
        unmatched=unmatched,
        first=first,
        last=last,
        speech_s=f"{speech_seconds:.0f}",
        matched_s=f"{matched_seconds:.0f}",
    )
    return BookSyncResult(
        BookSyncStatus.SUCCESS,
        out_srt=out_srt,
        cues=len(timings),
        unmatched_sentences=unmatched,
        speech_seconds=speech_seconds,
        matched_seconds=matched_seconds,
        timings=tuple(timings),
        unheard=tuple(gaps),
    )

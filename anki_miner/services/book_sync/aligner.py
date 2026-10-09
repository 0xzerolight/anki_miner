"""Align a Whisper transcript to a book's sentences, window by window.

Both sides are folded by :func:`normalize_for_alignment` and concatenated
into character streams. A transcript window of ``TRANSCRIPT_WINDOW_CHARS``
is aligned against a book window that starts at the cursor and is ~1.4× as
long (``rapidfuzz.distance.Levenshtein.opcodes`` — substitution cost 1 is
below an insert+delete pair, so a transcription slip costs one edit, as under
SubPlz's Needleman–Wunsch scores; long unbooked stretches are NOT what the
costs handle — the equal-only cursor rule and the probes below are). Only
the first ``COMMIT_FRACTION`` of the window is committed (the tail is ragged
where the book window ran long), the cursor moves to the last book sentence
touched, and the next window starts at the first uncommitted transcript
character.

The cursor advances only on EQUAL characters: unit-cost Levenshtein will
"replace" an unbooked transcript stretch against unread book text whenever
that is cheaper than deleting it, and a cursor moved by those replacements
would skip real sentences for good. A committed window whose equal share is
under the window floor means the two sides are not looking at the same
passage. Two unrelated passages already share a fraction of their characters
that depends on the alphabet, so both floors come from the book itself
(:func:`_null_floors`): its own null share plus a margin, never below
``MIN_WINDOW_MATCH`` / ``REANCHOR_MIN_SCORE``. Two probes, one attempt each,
each needing the re-anchor floor: the book window's first
``REANCHOR_PROBE_CHARS`` searched inside the transcript window (an unbooked
transcript prefix — narrator intro, chapter announcement — is dropped and the
window redone from the first booked character), then the transcript window's
first ``REANCHOR_PROBE_CHARS`` searched in the book ahead,
``REANCHOR_SPAN_CHARS`` at a time, nearest first (an unread preface or
skipped chapter — the cursor jumps there and the window is redone; a jump
that does not pay off is undone). If both fail the window's transcript is
skipped and the cursor stays.

Timing: a transcript character's time is linear interpolation inside its
segment; a sentence's cue is [time of its first matched char, time after its
last matched char], emitted only when it collected enough matches. Cues are
clipped monotonic and given a floor duration. The alignment is O(window²)
per window with bit-parallel rapidfuzz — milliseconds — so a whole audiobook
is seconds, and nothing the size of the book is ever allocated at once.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from anki_miner.services.book_sync.normalize import normalize_for_alignment

TRANSCRIPT_WINDOW_CHARS = 2000
COMMIT_FRACTION = 0.6
BOOK_WINDOW_SLACK = 1.4
BOOK_WINDOW_EXTRA = 400
MIN_WINDOW_MATCH = 0.35
REANCHOR_SPAN_CHARS = 30000
REANCHOR_PROBE_CHARS = 300
REANCHOR_MIN_SCORE = 60.0
MIN_SENTENCE_MATCH = 3
MIN_SENTENCE_MATCH_SHARE = 0.3
MIN_CUE_SECONDS = 0.3

#: Equal share two unrelated passages of the same book already reach depends on
#: the alphabet, so the off-track floor is the book's own null share plus a
#: margin, never below MIN_WINDOW_MATCH. Measured with this estimate on books
#: built from the 32 mining languages' fixture words: null kana 0.25-0.27,
#: es/de/ru 0.41-0.51, Latin/Cyrillic 0.38-0.54; unrelated windows reached
#: at most 0.083 above the null (real mixed-script text reached ~0.11 above;
#: the fixed floor sat under the Latin null itself) and noisy related reads
#: stayed at least 0.28 above it. The kana test fixture's floor stays
#: MIN_WINDOW_MATCH (null + margin 0.004 under it); other kana books can sit
#: up to ~0.02 above it. Probe scores: unrelated at most 6.5 above the null,
#: related at least 18.4 above it. With 5 samples the estimate is too noisy.
NULL_MARGIN = 0.09
REANCHOR_NULL_MARGIN = 12.0
NULL_SAMPLES = 8


@dataclass(frozen=True)
class TimedText:
    """One transcript segment: absolute seconds and its raw text."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class BookText:
    """A book as ordered sentences plus their folded keys (same length, same order)."""

    title: str
    sentences: tuple[str, ...]
    keys: tuple[str, ...]


@dataclass
class BookCursor:
    """First sentence not yet consumed; shared across the audio files of one book."""

    sentence: int = 0


@dataclass(frozen=True)
class SentenceTiming:
    index: int
    start: float
    end: float


class _Stream:
    """A folded character stream with, per character, its owner and position."""

    __slots__ = ("chars", "owner", "pos", "lengths")

    def __init__(self, keys: Sequence[str], first: int = 0) -> None:
        chars: list[str] = []
        owner: list[int] = []
        pos: list[int] = []
        lengths: dict[int, int] = {}
        for idx, key in enumerate(keys, start=first):
            lengths[idx] = len(key)
            for p, ch in enumerate(key):
                chars.append(ch)
                owner.append(idx)
                pos.append(p)
        self.chars = "".join(chars)
        self.owner = owner
        self.pos = pos
        self.lengths = lengths


def _book_stream(book: BookText, first: int, min_chars: int) -> _Stream:
    """Book stream from sentence ``first`` holding at least ``min_chars`` (or to the end)."""
    last = first
    total = 0
    while last < len(book.keys) and total < min_chars:
        total += len(book.keys[last])
        last += 1
    return _Stream(book.keys[first:last], first)


def _null_floors(book: BookText) -> tuple[float, float]:
    """(window floor, re-anchor floor) calibrated on ``book``.

    The null is what two passages of the book that are NOT the same text
    score: ``NULL_SAMPLES`` pairs, each a ``TRANSCRIPT_WINDOW_CHARS`` window
    standing in for the transcript and the book window that follows it (the
    gate's geometry), spread evenly over the book. The mean equal share and
    the mean prefix-probe score, plus their margins, are the floors, never
    below the constants. A book too short for one pair returns the constants.
    """
    window_chars = TRANSCRIPT_WINDOW_CHARS
    book_chars = int(window_chars * BOOK_WINDOW_SLACK) + BOOK_WINDOW_EXTRA
    commit = int(window_chars * COMMIT_FRACTION)
    last, tail = len(book.keys), 0  # last sentence a whole pair can start from
    while last > 0 and tail < window_chars + book_chars:
        last -= 1
        tail += len(book.keys[last])
    if tail < window_chars + book_chars:
        return MIN_WINDOW_MATCH, REANCHOR_MIN_SCORE
    shares: list[float] = []
    scores: list[float] = []
    for k in range(NULL_SAMPLES):
        first = k * last // (NULL_SAMPLES - 1)
        stand_in = _book_stream(book, first, window_chars)
        window = stand_in.chars[:window_chars]
        following = _book_stream(book, first + len(stand_in.lengths), book_chars).chars[:book_chars]
        if not following:
            continue  # one sentence holds the rest of the book
        equal = sum(
            min(op.src_end, commit) - op.src_start
            for op in Levenshtein.opcodes(window, following)
            if op.tag == "equal" and op.src_start < commit
        )
        shares.append(equal / commit)
        probe = fuzz.partial_ratio_alignment(following[:REANCHOR_PROBE_CHARS], window)
        scores.append(probe.score if probe is not None else 0.0)
    if not shares:
        return MIN_WINDOW_MATCH, REANCHOR_MIN_SCORE
    return (
        max(MIN_WINDOW_MATCH, statistics.fmean(shares) + NULL_MARGIN),
        max(REANCHOR_MIN_SCORE, statistics.fmean(scores) + REANCHOR_NULL_MARGIN),
    )


def _char_time(segments: Sequence[TimedText], stream: _Stream, i: int, *, after: bool = False) -> float:
    seg = segments[stream.owner[i]]
    length = max(1, stream.lengths[stream.owner[i]])
    p = stream.pos[i] + (1 if after else 0)
    return seg.start + (seg.end - seg.start) * min(p, length) / length


def align_to_book(
    segments: Sequence[TimedText],
    book: BookText,
    cursor: BookCursor,
    *,
    cancel_check: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[SentenceTiming]:
    """See the module docstring. Advances ``cursor`` past the last timed sentence."""
    transcript = _Stream([normalize_for_alignment(s.text) for s in segments])
    if not transcript.chars or cursor.sentence >= len(book.keys):
        return []
    window_floor, reanchor_floor = _null_floors(book)

    hits: dict[int, int] = {}
    first_char: dict[int, int] = {}
    last_char: dict[int, int] = {}

    i = 0
    n = len(transcript.chars)
    tried_prefix = False  # per window: the unbooked-transcript-prefix probe was tried
    tried_jump = False  # per window: the unread-book-stretch probe was tried
    jumped_from: int | None = None  # cursor before a jump; restored if the jump does not pay off
    while i < n:
        if cancel_check is not None and cancel_check():
            break
        window = transcript.chars[i : i + TRANSCRIPT_WINDOW_CHARS]
        is_last = i + len(window) >= n
        commit = len(window) if is_last else int(len(window) * COMMIT_FRACTION)
        if commit <= 0:
            break
        book_win = _book_stream(book, cursor.sentence, int(len(window) * BOOK_WINDOW_SLACK) + BOOK_WINDOW_EXTRA)
        if not book_win.chars:
            if jumped_from is not None:
                cursor.sentence = jumped_from  # the jump landed past the end; undo it
            break  # book exhausted

        matched = 0
        last_equal_book = -1
        local_hits: dict[int, int] = {}
        local_first: dict[int, int] = {}
        local_last: dict[int, int] = {}
        for op in Levenshtein.opcodes(window, book_win.chars):
            if op.tag not in ("equal", "replace"):
                continue
            i1, j1 = op.src_start, op.dest_start
            for k in range(i1, min(op.src_end, commit)):
                j = j1 + (k - i1)
                sentence = book_win.owner[j]
                t_index = i + k
                if op.tag == "equal":
                    matched += 1
                    local_hits[sentence] = local_hits.get(sentence, 0) + 1
                    last_equal_book = max(last_equal_book, j)
                local_first.setdefault(sentence, t_index)
                local_last[sentence] = t_index

        if matched / commit >= window_floor:
            for s, c in local_hits.items():
                hits[s] = hits.get(s, 0) + c
                first_char.setdefault(s, local_first[s])
                last_char[s] = max(last_char.get(s, -1), local_last[s])
            if last_equal_book >= 0:
                # Only an EQUAL character moves the cursor. Unit-cost Levenshtein
                # will "replace" an unbooked transcript stretch against unread
                # book text when that is cheaper than deleting it, and a cursor
                # advanced on those replacements would skip real sentences for
                # good (nothing ever moves it backwards).
                cursor.sentence = book_win.owner[last_equal_book]
            i += commit
            tried_prefix = tried_jump = False
            jumped_from = None
            continue

        # Off track. (a) Unbooked transcript prefix (narrator intro, chapter
        # announcement, "end of part one"): find where the book window STARTS
        # inside the transcript window and drop only that prefix. (b) Unread
        # book stretch (preface, skipped chapter): find where the transcript
        # window starts in the book ahead and jump the cursor there. One
        # attempt each per window; the shorter string must be the probe, or
        # rapidfuzz swaps the two and dest_start refers to the wrong side.
        if not tried_prefix:
            tried_prefix = True
            if len(window) > REANCHOR_PROBE_CHARS:
                hit = fuzz.partial_ratio_alignment(book_win.chars[:REANCHOR_PROBE_CHARS], window)
                if hit is not None and hit.score >= reanchor_floor and hit.dest_start > 0:
                    if log is not None:
                        log(f"Skipped {hit.dest_start} transcript characters with no match in the book")
                    i += hit.dest_start
                    continue  # redo from the first booked character
        if not tried_jump:
            tried_jump = True
            target = _find_ahead(window[:REANCHOR_PROBE_CHARS], book, cursor.sentence, reanchor_floor)
            if target is not None:
                jumped_from = cursor.sentence
                cursor.sentence = target
                if log is not None:
                    log(f"Re-anchored at sentence {cursor.sentence + 1}")
                continue  # redo against the book from the new cursor
        # Both probes tried (or inapplicable): give this window up. A jump that
        # did not pay off is undone — kept, it would skip every sentence in
        # between for good.
        if jumped_from is not None:
            cursor.sentence = jumped_from
        if log is not None:
            log(f"Skipped {commit} transcript characters with no match in the book")
        i += commit
        tried_prefix = tried_jump = False
        jumped_from = None

    return _emit(segments, transcript, book, cursor, hits, first_char, last_char)


def _find_ahead(probe: str, book: BookText, first: int, min_score: float) -> int | None:
    """Sentence where ``probe`` starts in the book at or after ``first``, or None.

    Searches ``REANCHOR_SPAN_CHARS`` at a time, nearest span first, so a hit
    close to the cursor wins over an equal one further on. Consecutive spans
    overlap by the probe's length, so a match across a span edge is not lost.
    """
    start = first
    while start < len(book.keys):
        search = _book_stream(book, start, REANCHOR_SPAN_CHARS)
        if len(search.chars) <= len(probe):
            return None
        hit = fuzz.partial_ratio_alignment(probe, search.chars)
        if hit is not None and hit.score >= min_score:
            return search.owner[min(hit.dest_start, len(search.owner) - 1)]
        if start + len(search.lengths) >= len(book.keys):
            return None  # this span reached the end of the book
        start = max(start + 1, search.owner[len(search.chars) - len(probe)])
    return None


def _emit(
    segments: Sequence[TimedText],
    transcript: _Stream,
    book: BookText,
    cursor: BookCursor,
    hits: dict[int, int],
    first_char: dict[int, int],
    last_char: dict[int, int],
) -> list[SentenceTiming]:
    timings: list[SentenceTiming] = []
    prev_end = -math.inf
    for s in sorted(hits):
        need = max(MIN_SENTENCE_MATCH, math.ceil(MIN_SENTENCE_MATCH_SHARE * len(book.keys[s])))
        if hits[s] < need:
            continue
        start = _char_time(segments, transcript, first_char[s])
        end = _char_time(segments, transcript, last_char[s], after=True)
        start = max(start, prev_end)
        end = max(end, start + MIN_CUE_SECONDS)
        timings.append(SentenceTiming(index=s, start=start, end=end))
        prev_end = end
    if timings:
        # Just past the last TIMED sentence, not max() with the loop's cursor:
        # the last window is committed whole, and a file cut mid-sentence ends
        # on a fragment whose stray equal characters can land in a later
        # sentence. Kept, that cursor makes the next file skip the sentences
        # in between for good.
        cursor.sentence = timings[-1].index + 1
    return timings

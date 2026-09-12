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
under ``MIN_WINDOW_MATCH`` means the two sides are not looking at the same
passage. Two probes, one attempt each: the book window's first
``REANCHOR_PROBE_CHARS`` searched inside the transcript window (an unbooked
transcript prefix — narrator intro, chapter announcement — is dropped and the
window redone from the first booked character), then the transcript window's
first ``REANCHOR_PROBE_CHARS`` searched in the next ``REANCHOR_SPAN_CHARS``
of book (an unread preface or skipped chapter — the cursor jumps there and
the window is redone; a jump that does not pay off is undone). If both fail
the window's transcript is skipped and the cursor stays.

Timing: a transcript character's time is linear interpolation inside its
segment; a sentence's cue is [time of its first matched char, time after its
last matched char], emitted only when it collected enough matches. Cues are
clipped monotonic and given a floor duration. The alignment is O(window²)
per window with bit-parallel rapidfuzz — milliseconds — so a whole audiobook
is seconds, and nothing the size of the book is ever allocated at once.
"""

from __future__ import annotations

import math
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

        if matched / commit >= MIN_WINDOW_MATCH:
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
                if hit is not None and hit.score >= REANCHOR_MIN_SCORE and hit.dest_start > 0:
                    if log is not None:
                        log(f"Skipped {hit.dest_start} transcript characters with no match in the book")
                    i += hit.dest_start
                    continue  # redo from the first booked character
        if not tried_jump:
            tried_jump = True
            probe = window[:REANCHOR_PROBE_CHARS]
            search = _book_stream(book, cursor.sentence, REANCHOR_SPAN_CHARS)
            if len(search.chars) > len(probe):
                hit = fuzz.partial_ratio_alignment(probe, search.chars)
                if hit is not None and hit.score >= REANCHOR_MIN_SCORE:
                    jumped_from = cursor.sentence
                    cursor.sentence = search.owner[min(hit.dest_start, len(search.owner) - 1)]
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
        cursor.sentence = max(cursor.sentence, timings[-1].index + 1)
    return timings

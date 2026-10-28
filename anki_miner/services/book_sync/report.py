"""How well a book synced: sentence %, audio % and a grade (MS-25), and what was not heard (MS-24).

Sentence % is the share of the book's sentences, from the first timed one to
the last, that got a cue: it says how much of what the audio covers was
found. Audio % is the share of the transcript's speech that falls inside a
cue: narration the book does not contain (intros, credits, a different
edition) lowers it. Each catches what the other misses: a sync that timed ten
sentences perfectly and lost the rest scores high on the first and low on
the second. The grade is the weaker of the two: A from 90 %, B from 75 %, C
from 50 %, D below. No weights (owner ruling R23; the idea is Storyteller's
alignment report, no code copied).

A sentence between two timed ones that got no cue was not heard (the narrator
skipped it, or a transcription hole). Its time can only be guessed from its
neighbours, so it never gets a cue — a guessed cue mined as a card would
carry the wrong audio — and :func:`unheard_gaps` reports where it probably is.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from anki_miner.services.book_sync.aligner import BookCursor, SentenceTiming

#: Lowest score (the weaker of sentence % and audio %) for each grade, best first.
GRADE_FLOORS: tuple[tuple[str, int], ...] = (("A", 90), ("B", 75), ("C", 50))


def grade_for(sentence_pct: int, audio_pct: int) -> str:
    """A-D for the weaker of the two percentages (see the module docstring)."""
    weaker = min(sentence_pct, audio_pct)
    for grade, floor in GRADE_FLOORS:
        if weaker >= floor:
            return grade
    return "D"


@dataclass(frozen=True)
class BookSyncReport:
    """One book's sync, summed over its audio files."""

    book_name: str
    #: Sentences that got a cue.
    sentences_timed: int
    #: Sentences from the first timed one to the last, inclusive.
    sentences_spanned: int
    #: Seconds of transcript speech, and how many of them a cue covers.
    speech_seconds: float
    matched_seconds: float

    @property
    def sentence_pct(self) -> int:
        return math.floor(100 * self.sentences_timed / self.sentences_spanned) if self.sentences_spanned else 0

    @property
    def audio_pct(self) -> int:
        return math.floor(100 * self.matched_seconds / self.speech_seconds) if self.speech_seconds > 0 else 0

    @property
    def grade(self) -> str:
        return grade_for(self.sentence_pct, self.audio_pct)


def build_report(
    book_name: str, cursor: BookCursor, speech_seconds: float, matched_seconds: float
) -> BookSyncReport | None:
    """The report for a book whose files all used ``cursor``; None when nothing was timed."""
    if not cursor.timed:
        return None
    return BookSyncReport(
        book_name=book_name,
        sentences_timed=len(cursor.timed),
        sentences_spanned=max(cursor.timed) - min(cursor.timed) + 1,
        speech_seconds=speech_seconds,
        matched_seconds=matched_seconds,
    )


def _union(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def speech_coverage(
    segments: Sequence[tuple[float, float, str]], timings: Sequence[SentenceTiming]
) -> tuple[float, float]:
    """(seconds of speech in ``segments``, seconds of it inside a cue)."""
    speech = _union((start, end) for start, end, text in segments if text.strip())
    cues = _union((t.start, t.end) for t in timings)
    total = sum(end - start for start, end in speech)
    covered = 0.0
    j = 0
    for start, end in speech:
        while j < len(cues) and cues[j][1] <= start:
            j += 1
        k = j
        while k < len(cues) and cues[k][0] < end:
            covered += min(end, cues[k][1]) - max(start, cues[k][0])
            k += 1
    return total, covered


@dataclass(frozen=True)
class UnheardGap:
    """Book sentences ``first``..``last`` (0-based) got no cue; the audio probably has them in ``start``..``end``."""

    first: int
    last: int
    start: float
    end: float


def unheard_gaps(timings: Sequence[SentenceTiming], known: Collection[int] = frozenset()) -> list[UnheardGap]:
    """Runs of untimed sentences between two timed ones whose cues are in book order.

    ``known`` holds the sentences an earlier file of the book timed: a file
    with a detour (MS-24) spans text another file already synced, so those
    sentences are never part of a gap, and a gap is split where they sit.
    """
    ordered = sorted(timings, key=lambda t: t.index)
    gaps: list[UnheardGap] = []
    for before, after in zip(ordered, ordered[1:], strict=False):
        if after.index <= before.index + 1 or after.start < before.end:
            continue
        run_first: int | None = None
        for i in range(before.index + 1, after.index + 1):
            if i < after.index and i not in known:
                if run_first is None:
                    run_first = i
            elif run_first is not None:
                gaps.append(UnheardGap(run_first, i - 1, before.end, after.start))
                run_first = None
    return gaps


def book_unheard(gaps: Iterable[UnheardGap], timed: Collection[int]) -> list[tuple[int, int]]:
    """A book's unheard sentences over all its files, as ``(first, last)`` runs in book order.

    Each file's gaps leave out what earlier files timed, but a later file can
    still time one of them (a passage read out of order, MS-24): the book's
    ``timed`` (its cursor's, once every file is done) is taken out again here.
    """
    runs: list[tuple[int, int]] = []
    for i in sorted({i for gap in gaps for i in range(gap.first, gap.last + 1)} - set(timed)):
        if runs and i == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs

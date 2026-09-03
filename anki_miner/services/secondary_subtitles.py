"""Secondary-language subtitle track: match its cues to a mined sentence window.

The mining-language subtitle is the primary track: it decides which words
are mined and what each card's sentence is. A second file in another
language (usually the user's own) rides beside it on the SAME video
timeline: shown under the primary cue in the curator preview, and written
to the card's Translation field. Nothing here parses or does I/O -- callers
hand in ``(start, end, text)`` cue tuples from
``SubtitleParserService.parse_raw_entries`` parsed at a ZERO offset, and the
second track's own offset is applied here, so the processor (card text) and
the curator (Translation column) run one rule on the same inputs and cannot
disagree.

Two independently timed tracks never line up exactly, so the join is by
overlap, not equality: every secondary cue overlapping the primary window by
at least ``min_overlap`` seconds attaches, in time order, joined by a single
space -- one primary line often spans two translated ones, and keeping only
the longest would lose half the sentence. A cue that merely touches the
window's edge (the normal case for neighbouring lines) does not attach.
"""

from __future__ import annotations

from collections.abc import Sequence

from anki_miner.models.word import TokenizedWord
from anki_miner.services.word_filter import CUE_JOINER

__all__ = ["DEFAULT_MIN_OVERLAP", "attach_translations", "match_secondary_line"]

#: Seconds a secondary cue must share with the primary window to count as
#: the same moment. Below it, an edge touch between neighbouring lines
#: attaches the wrong translation; above it, a genuinely short shared line
#: is lost. Capped at the window's own length, so a very short primary line
#: can still be matched.
DEFAULT_MIN_OVERLAP = 0.2


def match_secondary_line(
    entries: Sequence[tuple[float, float, str]],
    start: float,
    end: float,
    *,
    offset: float = 0.0,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> str:
    """Text of every secondary cue overlapping ``[start, end]``, in time order.

    ``entries`` are raw-timeline cues; ``offset`` shifts them onto the video
    timeline the way ``parse_raw_entries`` and the player do
    (``max(0, t + offset)``).
    """
    if not entries or end <= start:
        return ""
    threshold = min(min_overlap, end - start)
    hits: list[tuple[float, str]] = []
    for cue_start, cue_end, text in entries:
        shifted_start = max(0.0, cue_start + offset)
        shifted_end = max(0.0, cue_end + offset)
        overlap = min(end, shifted_end) - max(start, shifted_start)
        if overlap <= 0.0 or overlap < threshold:
            continue
        hits.append((shifted_start, text))
    hits.sort(key=lambda hit: hit[0])
    return CUE_JOINER.join(text for _start, text in hits)


def attach_translations(
    words: list[TokenizedWord],
    entries: Sequence[tuple[float, float, str]],
    *,
    offset: float = 0.0,
) -> None:
    """Set ``sentence_translation`` on every word from its own sentence window.

    Mutates in place, like ``WordFilterService.attach_occurrence_counts``.
    Overwrites unconditionally: run it AFTER anything that moves a word's
    window (a curator sentence pick, a line expansion) and the text follows.
    """
    for word in words:
        word.sentence_translation = match_secondary_line(entries, word.start_time, word.end_time, offset=offset)

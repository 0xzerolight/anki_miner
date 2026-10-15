"""Re-break long Whisper segments at sentence ends (MS-21).

faster-whisper hands back segments of up to ~30 s that often hold several
sentences. Generate Subtitles writes each as one cue, so a card made from it
carries a long clip and several sentences. With word timestamps on (the CT2
path always requests them, see ``transcriber._transcribe_ct2``), a long
segment is cut after every word that ends a sentence under the mining
language's own :class:`~anki_miner.languages.profile.SentenceRules`, and each
piece keeps its own words' times. CJK languages split at their punctuation
only: a word never ends a sentence by width or by a space. Idea from substudy
(``src/import/whisper.rs`` resegment); no code copied.

The whisper.cpp path has no per-word times (its ``token_timestamps`` is inert),
so its segments pass through unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

#: Only a segment longer than this is re-broken: a short one is already cue-sized.
REBREAK_MIN_SECONDS = 6.0
#: A piece shorter than this joins its neighbour: a lone "はい。" is no card.
MIN_PIECE_SECONDS = 1.0


def _ends_sentence(word: str, rules: SentenceRules) -> bool:
    """Whether ``word`` (one Whisper word, spacing included) closes a sentence."""
    text = word.strip().rstrip("".join(rules.closers))
    if not text or text[-1] not in rules.terminators:
        return False
    if rules.abbreviations and text.endswith("."):
        if text.endswith("..."):
            return False  # an ASCII ellipsis does not end the sentence (S8)
        stem = text[:-1].split()
        if stem and stem[-1].casefold() in rules.abbreviations:
            return False
    return True


def split_segment(
    start: float,
    end: float,
    text: str,
    words: Sequence[Any] | None,
    rules: SentenceRules | None,
) -> list[tuple[float, float, str]]:
    """Return ``(start, end, text)`` pieces of one segment; the segment itself when it stays whole.

    Args:
        start: Segment start in seconds.
        end: Segment end in seconds.
        text: Segment text (stripped on return).
        words: The segment's word objects (``.start``, ``.end``, ``.word``), or
            None when the engine gave none.
        rules: The mining language's sentence rules; None keeps every segment whole.
    """
    whole = [(start, end, text.strip())]
    if rules is None or not words or end - start <= REBREAK_MIN_SECONDS:
        return whole
    groups: list[list[Any]] = [[]]
    for word in words:
        groups[-1].append(word)
        if _ends_sentence(word.word, rules):
            groups.append([])
    if not groups[-1]:
        groups.pop()
    merged: list[list[Any]] = []
    for group in groups:
        if merged and (
            group[-1].end - group[0].start < MIN_PIECE_SECONDS
            or merged[-1][-1].end - merged[-1][0].start < MIN_PIECE_SECONDS
        ):
            merged[-1].extend(group)
        else:
            merged.append(group)
    if len(merged) < 2:
        return whole
    pieces: list[tuple[float, float, str]] = []
    for group in merged:
        piece_text = "".join(w.word for w in group).strip()
        if piece_text:
            pieces.append((float(group[0].start), float(group[-1].end), piece_text))
    return pieces if len(pieces) > 1 else whole

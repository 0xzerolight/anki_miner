"""Dialogue-only subtitle cleaning for alignment, and mapping results back.

Aligners (alass, ffsubsync) match cue timings against a reference signal.
Anything in a subtitle file that is not dialogue — signs, karaoke, OP/ED
lyrics, ♪ markers, hearing-impaired sound annotations — adds spans that have
nothing to do with speech and can drag the alignment onto a wrong optimum.
The community consensus for anime is unambiguous: strip non-dialogue from
BOTH sides before aligning.

Two sides, two entry points:

* :func:`clean_reference` — the extracted embedded track we align *against*.
  Produces a dialogue-only UTF-8 SRT (text is irrelevant to the aligner, so
  the format is normalized). Moved here from ``retime_reference``.
* :func:`clean_for_alignment` — the user's subtitle being retimed. Produces a
  same-format copy with non-dialogue cues *dropped* (never rewritten) and
  records which original cues survived, so the computed timings can be mapped
  back onto the untouched original with :func:`map_deltas_back` — every line
  and all ASS styling survive the round trip.

Nothing here raises for content reasons: an unparsable file returns None /
False and the caller falls back to aligning the original directly.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

import pysubs2

from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

logger = logging.getLogger(__name__)

__all__ = [
    "CleanedForAlignment",
    "clean_for_alignment",
    "clean_reference",
    "map_deltas_back",
]

#: ASS style names that mark an event as something other than dialogue. The
#: alternation is bounded by non-word characters on both sides so a style named
#: ``Subtitle`` does not match ``title`` and ``Fixed`` does not match ``ed``.
NON_DIALOGUE_STYLE_RE = re.compile(
    r"(?:^|[\W_])"
    r"(?:signs?|songs?|karaoke|kara|op|ed|opening|ending|credits?|titles?|captions?|notes?|typeset\w*)"
    r"(?:$|[\W_])",
    re.IGNORECASE,
)

#: ASS drawing-mode override. An event carrying one paints a vector shape, not
#: text, and its timing has nothing to do with speech.
ASS_DRAWING_RE = re.compile(r"\\p[1-9]")

#: One parenthesized group, JP or ASCII parens. Japanese HoH subs annotate
#: sounds as ``（雷鳴）`` and furigana as ``漢字(かんじ)``; English HoH subs
#: use ``(sighs)``. Removing the groups and checking what remains classifies
#: annotation-only cues without touching mixed speech lines.
_PAREN_GROUP_RE = re.compile(r"[（(][^（）()]*[）)]")

#: What may remain in a cue that carries no speech: whitespace, music glyphs,
#: prolonged-sound/dash marks, ellipses and bare punctuation.
_NON_SPEECH_RESIDUE_RE = re.compile(r"^[\s♪♫♬♩〜～ー―—\-…・.。、,!?！？~]*$")

#: Minimum surviving cues for a cleaned copy to be worth aligning. Below this
#: the drop list itself is suspect (wrong style names, annotation-heavy file)
#: and aligning the original unchanged is safer.
_MIN_ALIGNMENT_CUES = 10


def _is_non_speech_text(plaintext: str) -> bool:
    """True when a cue's visible text carries no speech to align against."""
    return bool(_NON_SPEECH_RESIDUE_RE.match(_PAREN_GROUP_RE.sub("", plaintext)))


def _is_non_dialogue_event(event: pysubs2.SSAEvent) -> bool:
    """True for cues an aligner should not see: comments, drawings,
    non-dialogue styles, zero/negative spans, and annotation/music-only text."""
    if getattr(event, "is_comment", None) is True:
        return True
    if event.end <= event.start:
        return True
    if NON_DIALOGUE_STYLE_RE.search(event.style or ""):
        return True
    if ASS_DRAWING_RE.search(event.text or ""):
        return True
    return _is_non_speech_text(event.plaintext.strip())


def _load(path: Path) -> pysubs2.SSAFile:
    try:
        return pysubs2.load(str(path))
    except UnicodeDecodeError as exc:
        return load_with_fallback_encoding(path, exc)


@dataclass(frozen=True)
class ReferenceCleanResult:
    """What survived cleaning a reference track: cue count and covered span."""

    cues: int
    span_ms: int


def clean_reference(src: Path, dest: Path) -> ReferenceCleanResult:
    """Write the dialogue-only cues of *src* to *dest* as UTF-8 SRT.

    Returns the surviving cue count and their first-start-to-last-end span —
    the two signals reference selection uses to reject a track that is not
    real full-episode dialogue. Drops everything
    :func:`_is_non_dialogue_event` flags, plus duplicate spans. Only cue
    *timings* matter to the aligner, but the text is carried through so the
    temp file stays readable when a run needs diagnosing.
    """
    subs = _load(src)

    kept: list[pysubs2.SSAEvent] = []
    seen: set[tuple[int, int]] = set()
    for event in subs.events:
        if _is_non_dialogue_event(event):
            continue
        span = (event.start, event.end)
        if span in seen:
            continue
        seen.add(span)
        # SRT has no concept of an empty cue; substitute a marker so the writer
        # cannot merge or drop a span whose text was pure override tags.
        text = event.plaintext.strip() or "-"
        kept.append(pysubs2.SSAEvent(start=event.start, end=event.end, text=text))

    out = pysubs2.SSAFile()
    out.events = kept
    out.save(str(dest), encoding="utf-8", format_="srt")
    span_ms = max(e.end for e in kept) - min(e.start for e in kept) if kept else 0
    return ReferenceCleanResult(cues=len(kept), span_ms=span_ms)


@dataclass(frozen=True)
class CleanedForAlignment:
    """A dialogue-only copy of the subtitle being retimed.

    ``kept_indices[i]`` is the index into the original event list of the i-th
    cue in *path* — the contract :func:`map_deltas_back` uses to attach the
    aligner's output timings back onto the original file.
    """

    path: Path
    kept_indices: list[int]
    total_events: int

    @property
    def dropped(self) -> int:
        return self.total_events - len(self.kept_indices)


def clean_for_alignment(src: Path, dest: Path) -> CleanedForAlignment | None:
    """Write a same-format copy of *src* to *dest* with non-dialogue cues dropped.

    Cues are only ever dropped, never rewritten, so the copy stays a strict
    subset the aligner sees in original order. Returns None when the file
    cannot be parsed or too few cues survive — the caller then aligns the
    original file directly, which is exactly the pre-cleaning behaviour.
    """
    try:
        subs = _load(src)
    except Exception:  # noqa: BLE001 — an unparsable input falls back to as-is alignment
        logger.warning("subtitle cleaner: could not parse %s", src, exc_info=True)
        return None

    total = len(subs.events)
    kept_indices = [i for i, event in enumerate(subs.events) if not _is_non_dialogue_event(event)]
    if len(kept_indices) < _MIN_ALIGNMENT_CUES:
        logger.info(
            "subtitle cleaner: only %d of %d cues look like dialogue in %s; aligning uncleaned",
            len(kept_indices),
            total,
            src.name,
        )
        return None

    kept_set = set(kept_indices)
    cleaned = pysubs2.SSAFile()
    cleaned.styles = subs.styles.copy()
    cleaned.info = subs.info.copy()
    cleaned.events = [event.copy() for i, event in enumerate(subs.events) if i in kept_set]
    try:
        cleaned.save(str(dest), encoding="utf-8")
    except Exception:  # noqa: BLE001 — an unwritable copy falls back to as-is alignment
        logger.warning("subtitle cleaner: could not write %s", dest, exc_info=True)
        return None
    return CleanedForAlignment(path=dest, kept_indices=kept_indices, total_events=total)


def map_deltas_back(
    original: Path,
    synced_clean: Path,
    kept_indices: list[int],
    out: Path,
) -> bool:
    """Apply the aligner's timing changes to the untouched *original* file.

    *synced_clean* is the aligner's output for the cleaned copy; its i-th cue
    corresponds to original event ``kept_indices[i]``. Kept cues take their
    exact new timings. Dropped cues take the start/end delta of the nearest
    kept cue by original start time — aligners shift in per-block constants,
    so the nearest anchor's shift is the block-correct one, where linear
    interpolation across a block boundary would land between blocks.

    Writes the result to *out* in the original's format (styles and all lines
    preserved). Returns False when the cue counts do not match — the aligner
    dropped or merged cues and the mapping would be wrong.
    """
    try:
        subs = _load(original)
        synced = _load(synced_clean)
    except Exception:  # noqa: BLE001 — parse failure means the candidate is unusable
        logger.warning("subtitle cleaner: map-back parse failed", exc_info=True)
        return False

    if len(synced.events) != len(kept_indices):
        logger.warning(
            "subtitle cleaner: aligner changed cue count (%d cleaned, %d synced); discarding",
            len(kept_indices),
            len(synced.events),
        )
        return False
    if any(i >= len(subs.events) for i in kept_indices):
        logger.warning("subtitle cleaner: kept indices no longer fit the original; discarding")
        return False

    # Anchors sorted by original start time for nearest-neighbour lookup.
    anchors = sorted(
        (
            subs.events[orig_i].start,
            synced_event.start - subs.events[orig_i].start,
            synced_event.end - subs.events[orig_i].end,
        )
        for orig_i, synced_event in zip(kept_indices, synced.events, strict=True)
    )
    starts = [a[0] for a in anchors]

    kept_set = set(kept_indices)
    by_original_index = dict(zip(kept_indices, synced.events, strict=True))
    for i, event in enumerate(subs.events):
        if i in kept_set:
            event.start = by_original_index[i].start
            event.end = by_original_index[i].end
            continue
        pos = bisect_left(starts, event.start)
        if pos <= 0:
            _, delta_start, delta_end = anchors[0]
        elif pos >= len(anchors):
            _, delta_start, delta_end = anchors[-1]
        else:
            before, after = anchors[pos - 1], anchors[pos]
            nearest = before if event.start - before[0] <= after[0] - event.start else after
            _, delta_start, delta_end = nearest
        event.start = max(0, event.start + delta_start)
        event.end = max(0, event.end + delta_end)

    try:
        subs.save(str(out), encoding="utf-8")
    except Exception:  # noqa: BLE001 — write failure means the candidate is unusable
        logger.warning("subtitle cleaner: could not write %s", out, exc_info=True)
        return False
    return True

"""Sanity-check a synced subtitle candidate before it may replace the original.

Aligners fail loud in the files but silent in their exit codes: a run that
locked onto the wrong optimum still exits 0 and writes a syntactically valid
subtitle whose every cue is minutes off. This module compares the candidate
against the original (and the video's duration when known) and rejects the
failure signatures documented across alass's issue tracker:

* implausibly large shifts (wrong-reference / wrong-episode alignment),
* cue order scrambled by divergent split blocks (alass #50),
* negative timestamps clamped into a pile-up at 00:00 (alass #7, #13),
* cues shifted past the end of the video,
* total span stretched/compressed beyond any legitimate framerate ratio,
* the engine's own suspicion signals (:class:`SyncResult.warnings`).

The contract the orchestrator builds on: a candidate that fails here is
discarded and the original file is left untouched — a bad sync is strictly
worse than no sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from anki_miner.services.subtitle_cleaner import _load
from anki_miner.services.sync_engines import SyncResult

logger = logging.getLogger(__name__)

__all__ = ["ValidationVerdict", "validate_candidate"]

#: Largest believable correction. Cross-release drift and ad-break offsets sit
#: in seconds-to-a-couple-of-minutes territory; a five-minute cue shift means
#: the aligner matched the wrong content entirely.
_MAX_SHIFT_MS = 5 * 60 * 1000

#: More than this many cues stacked at 00:00 that were not there originally
#: means negative timestamps got clamped (the aligner shifted the head of the
#: file before zero).
_MAX_ZERO_PILEUP = 2

#: Legitimate framerate corrections are near 1.0 (25/23.976 ≈ 1.043 is the
#: largest common ratio). A total-span change outside these bounds is a bogus
#: stretch, not a framerate fix.
_SPAN_RATIO_BOUNDS = (0.8, 1.25)

#: Grace beyond the video's end before a cue counts as out of bounds — real
#: releases carry cues that outlive the last frame by a moment.
_DURATION_EPSILON_MS = 10 * 1000


@dataclass(frozen=True)
class ValidationVerdict:
    """The validator's decision for one candidate; ``reasons`` explains a no."""

    ok: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def validate_candidate(
    original: Path,
    candidate: Path,
    result: SyncResult,
    *,
    video_duration_seconds: float | None = None,
) -> ValidationVerdict:
    """Judge whether *candidate* is a trustworthy retime of *original*.

    Never raises: an unreadable candidate is simply rejected, and an
    unreadable *original* rejects too (with nothing to compare against, a
    claim of success would be a guess).
    """
    reasons: list[str] = []

    if not result.ok:
        reasons.append(f"{result.engine}: {result.detail or 'engine reported failure'}")
        return ValidationVerdict(False, tuple(reasons))
    reasons.extend(f"{result.engine}: {warning}" for warning in result.warnings)

    try:
        orig_events = _load(original).events
        cand_events = _load(candidate).events
    except Exception:  # noqa: BLE001 — unreadable file means unverifiable candidate
        logger.warning("sync validator: could not parse candidate or original", exc_info=True)
        return ValidationVerdict(False, (*reasons, "candidate or original unparsable"))

    if len(cand_events) != len(orig_events):
        reasons.append(f"cue count changed: {len(orig_events)} -> {len(cand_events)}")
        return ValidationVerdict(False, tuple(reasons))
    if not cand_events:
        reasons.append("candidate has no cues")
        return ValidationVerdict(False, tuple(reasons))

    max_shift = max(abs(c.start - o.start) for c, o in zip(cand_events, orig_events, strict=True))
    if max_shift > _MAX_SHIFT_MS:
        reasons.append(f"max cue shift {max_shift / 1000:.1f}s exceeds {_MAX_SHIFT_MS / 1000:.0f}s")

    if any(abs(shift) * 1000 > _MAX_SHIFT_MS for shift in result.block_shifts_seconds):
        reasons.append("a shifted block moved beyond the plausible-offset bound")

    orig_starts = [e.start for e in orig_events]
    if orig_starts == sorted(orig_starts):
        cand_starts = [e.start for e in cand_events]
        if cand_starts != sorted(cand_starts):
            reasons.append("cue order scrambled by divergent block shifts")

    pileup = sum(1 for c, o in zip(cand_events, orig_events, strict=True) if c.start == 0 and o.start > 0)
    if pileup > _MAX_ZERO_PILEUP:
        reasons.append(f"{pileup} cues clamped to 00:00 (negative timestamps)")

    if video_duration_seconds is not None and video_duration_seconds > 0:
        limit = video_duration_seconds * 1000 + _DURATION_EPSILON_MS
        beyond = sum(1 for e in cand_events if e.start > limit)
        if beyond:
            reasons.append(f"{beyond} cues start past the end of the video")

    orig_span = orig_events[-1].end - orig_events[0].start
    cand_span = cand_events[-1].end - cand_events[0].start
    if orig_span > 0 and cand_span > 0:
        ratio = cand_span / orig_span
        low, high = _SPAN_RATIO_BOUNDS
        if not (low <= ratio <= high):
            reasons.append(f"total span scaled by {ratio:.2f} (outside {low}-{high})")

    return ValidationVerdict(not reasons, tuple(reasons))

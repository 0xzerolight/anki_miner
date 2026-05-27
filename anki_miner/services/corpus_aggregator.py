"""Corpus aggregation and lemma selection for the deck-builder feature.

Stateless functions; no I/O except through the injected ``SubtitleParserService``
in :func:`aggregate`.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

from anki_miner.models.deck_build import DeckBuildPreview, DeckSelectionMode

if TYPE_CHECKING:
    from anki_miner.services.subtitle_parser import SubtitleParserService
    from anki_miner.utils.file_pairing import FilePair


def aggregate(
    parser: SubtitleParserService,
    pairs: list[FilePair],
) -> collections.Counter[str]:
    """Sum per-file lemma counts across all file pairs into a single corpus Counter.

    Args:
        parser: Subtitle parser service providing :meth:`count_lemmas`.
        pairs: List of video/subtitle file pairs to aggregate.

    Returns:
        Combined ``Counter`` mapping lemma → total occurrence count across the
        whole corpus.  An empty list of pairs returns an empty ``Counter``.
    """
    combined: collections.Counter[str] = collections.Counter()
    for pair in pairs:
        combined.update(parser.count_lemmas(pair.subtitle))
    return combined


def select(
    counts: collections.Counter[str],
    mode: DeckSelectionMode,
    value: float,
    known_lemmas: set[str],
) -> tuple[set[str], DeckBuildPreview]:
    """Select candidate lemmas from a corpus counter and compute a build preview.

    The ``known_lemmas`` set is used **only** to populate ``known_skipped`` and
    ``card_count`` in the preview — it does NOT shrink the returned candidate set.
    Pass an empty set to model "mine everything".

    Args:
        counts: Lemma → occurrence count for the full corpus.
        mode: Selection strategy (:class:`~anki_miner.models.deck_build.DeckSelectionMode`).
        value: Interpreted as *N* (``TOP_N``, fractional parts truncated via
            ``int()``) or target percentage 0–100 (``COVERAGE_PCT``); ignored
            for ``ALL``.  ``TOP_N`` ≤ 0 and ``COVERAGE_PCT`` ≤ 0 select nothing.
        known_lemmas: Lemmas already in the user's Anki collection.

    Returns:
        A ``(candidate_set, preview)`` tuple.  ``candidate_set`` is a plain
        ``set[str]``; ``preview`` is a frozen :class:`~anki_miner.models.deck_build.DeckBuildPreview`.
    """
    total_tokens: int = sum(counts.values())
    unique_lemmas: int = len(counts)

    # Empty corpus — avoid ZeroDivisionError and short-circuit.
    if total_tokens == 0:
        return set(), DeckBuildPreview(
            total_tokens=0,
            unique_lemmas=0,
            candidate_count=0,
            projected_coverage_pct=0.0,
            known_skipped=0,
            card_count=0,
        )

    # Rank descending by count; ties resolve by first-insertion order (stable sort).
    ranked: list[str] = sorted(counts, key=lambda lemma: -counts[lemma])

    candidate_set: set[str]

    if mode is DeckSelectionMode.ALL:
        candidate_set = set(ranked)

    elif mode is DeckSelectionMode.TOP_N:
        n = int(value)
        candidate_set = set() if n <= 0 else set(ranked[:n])

    elif mode is DeckSelectionMode.COVERAGE_PCT:
        target = value / 100.0
        candidate_set = set()
        if target > 0.0:
            # Smallest prefix whose cumulative coverage reaches the target.
            cumulative = 0
            for lemma in ranked:
                cumulative += counts[lemma]
                candidate_set.add(lemma)
                if cumulative / total_tokens >= target:
                    break

    else:  # pragma: no cover - exhaustiveness guard
        raise ValueError(f"Unhandled selection mode: {mode!r}")

    candidate_count = len(candidate_set)
    projected_coverage_pct = (sum(counts[lemma] for lemma in candidate_set) / total_tokens) * 100.0
    known_skipped = len(candidate_set & known_lemmas)
    card_count = candidate_count - known_skipped

    return candidate_set, DeckBuildPreview(
        total_tokens=total_tokens,
        unique_lemmas=unique_lemmas,
        candidate_count=candidate_count,
        projected_coverage_pct=projected_coverage_pct,
        known_skipped=known_skipped,
        card_count=card_count,
    )

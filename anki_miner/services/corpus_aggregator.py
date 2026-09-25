"""Corpus aggregation, pure ranking and preview maths for the deck-builder feature.

Stateless functions; no I/O except through the injected ``SubtitleParserService``
in :func:`aggregate`.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

from anki_miner.models.deck_build import DeckBuildPreview, DeckCorpus, DeckSelectionMode

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from anki_miner.models.word import TokenizedWord
    from anki_miner.services.subtitle_parser import SubtitleParserService


def aggregate(
    parser: SubtitleParserService,
    subtitles: Iterable[Path],
    cancel_check: Callable[[], bool] | None = None,
) -> collections.Counter[str]:
    """Sum per-file lemma counts across subtitles into a single corpus Counter.

    Args:
        parser: Subtitle parser service providing :meth:`count_lemmas`.
        subtitles: Subtitle file paths to aggregate, in scan order.
        cancel_check: Optional callable polled before each file; returning
            True stops aggregation early. This is the longest deck-builder
            Phase-1 step (MeCab over the whole corpus), so a cancel must be
            able to interrupt it between files. The partial ``Counter`` is
            returned; the caller is expected to re-check cancellation and
            discard it.

    Returns:
        Combined ``Counter`` mapping lemma -> total occurrence count across
        the whole corpus. An empty ``subtitles`` iterable returns an empty
        ``Counter``.
    """
    combined: collections.Counter[str] = collections.Counter()
    for subtitle in subtitles:
        if cancel_check is not None and cancel_check():
            break
        combined.update(parser.count_lemmas(subtitle))
    return combined


def rank_select(
    counts: Mapping[str, int],
    mode: DeckSelectionMode,
    value: float,
) -> set[str]:
    """Rank corpus lemmas by occurrence and select a candidate set.

    Ranks descending by count; ties resolve by first-insertion order (stable
    sort), so callers get deterministic results from a ``Counter`` built by
    :func:`aggregate`.

    Args:
        counts: Lemma -> occurrence count for the full corpus.
        mode: Selection strategy.
        value: Interpreted as *N* (``TOP_N``, fractional parts truncated via
            ``int()``) or target percentage 0-100 (``COVERAGE_PCT``);
            ignored for ``ALL``. ``TOP_N`` <= 0 and ``COVERAGE_PCT`` <= 0
            select nothing.

    Returns:
        The selected lemma set. An empty ``counts`` mapping returns an empty
        set for every mode.
    """
    total_tokens = sum(counts.values())
    if total_tokens == 0:
        return set()

    ranked: list[str] = sorted(counts, key=lambda lemma: -counts[lemma])

    if mode is DeckSelectionMode.ALL:
        return set(ranked)

    if mode is DeckSelectionMode.TOP_N:
        n = int(value)
        return set() if n <= 0 else set(ranked[:n])

    if mode is DeckSelectionMode.COVERAGE_PCT:
        target = value / 100.0
        selected: set[str] = set()
        if target > 0.0:
            # Smallest prefix whose cumulative coverage reaches the target.
            cumulative = 0
            for lemma in ranked:
                cumulative += counts[lemma]
                selected.add(lemma)
                if cumulative / total_tokens >= target:
                    break
        return selected

    raise ValueError(f"Unhandled selection mode: {mode!r}")  # pragma: no cover - exhaustiveness guard


def row_lemmas(word: TokenizedWord) -> frozenset[str]:
    """Lemmas a season-pool row can card: its own lemma plus every sentence-candidate's.

    A row with alternate sentence picks (:attr:`TokenizedWord.sentence_candidates`)
    can card under any of those lemmas too — e.g. a 頭髮/头发 variant pair sharing
    one row.
    """
    return frozenset({word.lemma}) | {candidate.lemma for candidate in word.sentence_candidates}


def build_preview(corpus: DeckCorpus, selected: set[str]) -> DeckBuildPreview:
    """Compute a preview of what building a deck from ``selected`` would produce.

    Coverage % is ``sum(counts[lemma] for lemma in selected) / total_tokens``
    over the corpus's own mineable-word token counts (from
    ``SubtitleParserService.count_lemmas``), NOT over ``frequency.csv``. The
    build must reproduce this raw-lemma preview — diverging here is the
    "promised 2,401, built 51" bug (see ``deck_builder_worker.py``).

    Args:
        corpus: Aggregated per-season corpus.
        selected: Candidate lemma set, e.g. from :func:`rank_select`.

    Returns:
        The computed :class:`DeckBuildPreview`. An empty corpus (no tokens)
        returns all zeros.
    """
    total = sum(corpus.counts.values())
    if total == 0:
        return DeckBuildPreview(0, 0, 0, 0.0, 0, 0)
    covered = sum(corpus.counts.get(lemma, 0) for lemma in selected)
    carded = set().union(*corpus.row_lemmas) if corpus.row_lemmas else set()
    return DeckBuildPreview(
        total_tokens=total,
        unique_lemmas=len(corpus.counts),
        candidate_count=len(selected),
        projected_coverage_pct=covered / total * 100.0,
        known_skipped=len(selected - carded),
        card_count=sum(1 for lemmas in corpus.row_lemmas if lemmas & selected),
    )

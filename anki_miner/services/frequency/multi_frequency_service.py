"""Additive aggregation across enabled frequency sources.

Wraps an ordered list of already-loaded :class:`IndexedFreqProvider` instances
and layers them additively:

* :meth:`lookup_all` returns every source that has a rank for the term, in chain
  order — the per-source breakdown shown on the card.
* :func:`min_rank` reduces a fetched ``lookup_all`` list to the best (minimum)
  rank, driving the top-N frequency filter.
* :func:`harmonic_rank` reduces the same list to the harmonic mean of the
  per-source ranks (Yomitan's ``getFrequencyHarmonic``), driving the card's
  numeric sort field.

Providers are assembled and ``.load()``-ed by ``FrequencySourceRegistry``; this
service only reads them.
"""

from __future__ import annotations

import contextlib
import logging
import math
from fractions import Fraction

from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from anki_miner.services.frequency.storage import CATEGORICAL_RANK

logger = logging.getLogger(__name__)

# Per-source lookup result: (provider name, rank, display_value), as returned by
# MultiFrequencyService.lookup_all / IndexedFreqProvider.lookup_detail.
FreqSources = list[tuple[str, int, str | None]]


def min_rank(sources: FreqSources) -> int | None:
    """Minimum rank across an already-fetched ``lookup_all`` result, or None.

    Pure derivation over the per-source list so a caller that already holds the
    breakdown (e.g. ``EpisodeProcessor._phase2_filter``) can compute the top-N
    filter's rank without re-running the per-source SQL. Word-based (categorical)
    sources carry the ``CATEGORICAL_RANK`` sentinel and are excluded — their level
    labels must not participate in the numeric rank cutoff.
    """
    ranks = [rank for _name, rank, _display in sources if rank < CATEGORICAL_RANK]
    return min(ranks) if ranks else None


def harmonic_rank(sources: FreqSources) -> int | None:
    """Harmonic mean of an already-fetched ``lookup_all`` result, or None.

    Yomitan ``getFrequencyHarmonic`` (upstream e2ed450): ``floor(n / Σ(1/f))``
    over one rank per source, dropping non-positive ranks (which also rules out a
    divide-by-zero). ``lookup_all`` yields at most one value per source, so the
    one-value-per-dictionary dedup holds by construction. Pure derivation so the
    sort field can be computed from a single fetch — see :func:`min_rank`.
    Categorical sources (``CATEGORICAL_RANK`` sentinel) are excluded so they do
    not inflate the harmonic count ``n``.

    Computed with exact rational arithmetic — a deliberate deviation from
    Yomitan's JS float math, whose ``1/(1/f)`` round-trip truncates to ``f-1``
    for ~8% of single-source ranks (e.g. 29814 → 29813) and made the card's
    sort field disagree with the displayed rank. The algorithm (floor of the
    harmonic mean) is unchanged; only the float noise is gone.
    """
    ranks = [rank for _name, rank, _display in sources if 0 < rank < CATEGORICAL_RANK]
    if not ranks:
        return None
    total = sum(Fraction(1, rank) for rank in ranks)
    return math.floor(len(ranks) / total)


class MultiFrequencyService:
    """Aggregates term -> rank lookups across an ordered set of providers."""

    def __init__(self, providers: list[IndexedFreqProvider]):
        self._providers = providers

    def is_available(self) -> bool:
        """True if any wrapped provider is available."""
        return any(p.is_available() for p in self._providers)

    def has_numeric_source(self) -> bool:
        """True if any loaded provider is a numeric (non-categorical) source.

        The ``max_frequency_rank`` cutoff must gate on this, NOT ``is_available``:
        a chain whose only loaded source is categorical (JLPT-band etc.) reports
        every row at ``CATEGORICAL_RANK``, which ``min_rank`` excludes — so every
        word ends up rank None and ``filter_by_frequency`` would drop 100% of them.
        Only a numeric source can produce a rank the cutoff can meaningfully apply.
        """
        return any(p.is_available() and not p.is_categorical for p in self._providers)

    def lookup_all(self, term: str, reading: str | None = None) -> list[tuple[str, int, str | None]]:
        """``(provider name, rank, display_value)`` for each provider ranking ``term``.

        Returned in provider (chain) order; providers with no rank are omitted.
        This is the per-source breakdown rendered on the card — ``display_value``
        is the human string a card shows in place of the bare rank (None for
        plain-int/CSV ranks or v1 indexes). ``reading`` scopes the per-source
        lookup so homographs no longer inherit each other's ranks.
        """
        results: list[tuple[str, int, str | None]] = []
        for provider in self._providers:
            detail = provider.lookup_detail(term, reading)
            if detail is not None:
                rank, display_value = detail
                results.append((provider.name, rank, display_value))
        return results

    def lookup_all_many(self, pairs: list[tuple[str, str | None]]) -> list[FreqSources]:
        """Per-pair :meth:`lookup_all` results; ``results[i]`` is byte-identical
        to ``lookup_all(*pairs[i])`` (chain order, misses omitted).

        Batch fast-path for the orchestrator's frequency-attachment phase: one
        IN-clause query per provider per chunk (see
        :meth:`IndexedFreqProvider.lookup_detail_many`) instead of one query per
        (word, provider).
        """
        results: list[FreqSources] = [[] for _ in pairs]
        for provider in self._providers:
            for i, detail in enumerate(provider.lookup_detail_many(pairs)):
                if detail is not None:
                    results[i].append((provider.name, detail[0], detail[1]))
        return results

    def close(self) -> None:
        """Close every wrapped provider's sqlite handle.

        A fresh service + providers are built per mining run, so the persistent
        ``index.sqlite`` connections each :class:`IndexedFreqProvider` holds must
        be released on processor teardown — otherwise handles leak until GC and,
        on Windows, a "mine then Settings → Remove source" sequence file-locks
        ``freqs_root/<id>/index.sqlite`` (the dictionary-side Issue #30 class).

        Idempotent and never raises: ``IndexedFreqProvider.close()`` is itself
        safe to call twice, a provider lacking ``close`` is skipped, and a
        raising provider does not stop the others from closing.
        """
        for provider in self._providers:
            close = getattr(provider, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

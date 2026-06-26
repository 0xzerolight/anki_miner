"""Additive aggregation across enabled frequency sources.

Wraps an ordered list of already-loaded :class:`IndexedFreqProvider` instances
and layers them additively:

* :meth:`lookup_min` returns the best (minimum) rank any source reports, driving
  frequency filtering and the card's sort field.
* :meth:`lookup_all` returns every source that has a rank for the term, in chain
  order — the per-source breakdown shown on the card.

Providers are assembled and ``.load()``-ed by ``FrequencySourceRegistry``; this
service only reads them.
"""

from __future__ import annotations

import logging

from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)

logger = logging.getLogger(__name__)


class MultiFrequencyService:
    """Aggregates term -> rank lookups across an ordered set of providers."""

    def __init__(self, providers: list[IndexedFreqProvider]):
        self._providers = providers

    def is_available(self) -> bool:
        """True if any wrapped provider is available."""
        return any(p.is_available() for p in self._providers)

    def lookup_all(self, term: str) -> list[tuple[str, int]]:
        """``(provider name, rank)`` for each provider that ranks ``term``.

        Returned in provider (chain) order; providers with no rank are omitted.
        This is the per-source breakdown rendered on the card.
        """
        results: list[tuple[str, int]] = []
        for provider in self._providers:
            rank = provider.lookup(term)
            if rank is not None:
                results.append((provider.name, rank))
        return results

    def lookup_min(self, term: str) -> int | None:
        """Minimum rank across all providers, or None if none rank ``term``."""
        ranks = [rank for _name, rank in self.lookup_all(term)]
        return min(ranks) if ranks else None

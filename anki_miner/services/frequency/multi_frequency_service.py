"""Additive aggregation across enabled frequency sources.

Wraps an ordered list of already-loaded :class:`IndexedFreqProvider` instances
and layers them additively:

* :meth:`lookup_min` returns the best (minimum) rank any source reports, driving
  the top-N frequency filter.
* :meth:`lookup_harmonic` returns the harmonic mean of the per-source ranks
  (Yomitan's ``getFrequencyHarmonic``), driving the card's numeric sort field.
* :meth:`lookup_all` returns every source that has a rank for the term, in chain
  order — the per-source breakdown shown on the card.

Providers are assembled and ``.load()``-ed by ``FrequencySourceRegistry``; this
service only reads them.
"""

from __future__ import annotations

import contextlib
import logging
import math

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

    def lookup_all(self, term: str, reading: str | None = None) -> list[tuple[str, int]]:
        """``(provider name, rank)`` for each provider that ranks ``term``.

        Returned in provider (chain) order; providers with no rank are omitted.
        This is the per-source breakdown rendered on the card. ``reading`` scopes
        the per-source lookup so homographs no longer inherit each other's ranks.
        """
        results: list[tuple[str, int]] = []
        for provider in self._providers:
            rank = provider.lookup(term, reading)
            if rank is not None:
                results.append((provider.name, rank))
        return results

    def lookup_min(self, term: str, reading: str | None = None) -> int | None:
        """Minimum rank across all providers, or None if none rank ``term``."""
        ranks = [rank for _name, rank in self.lookup_all(term, reading)]
        return min(ranks) if ranks else None

    def lookup_harmonic(self, term: str, reading: str | None = None) -> int | None:
        """Harmonic mean of the per-source ranks, or None if none rank ``term``.

        Ported from Yomitan getFrequencyHarmonic
        (ext/js/data/anki-note-data-creator.js, upstream commit e2ed450):
        ``floor(n / Σ(1/f))`` over one rank per source. ``lookup_all`` already
        yields at most one value per source, so Yomitan's one-value-per-
        dictionary dedup holds by construction. Non-positive ranks are dropped
        (mirrors upstream ``getFrequencyNumbers``' ``frequency > 0`` guard),
        which also rules out a divide-by-zero. This drives the card's numeric
        ``frequency_sort`` field so no single niche source dominates the sort the
        way a bare MIN can; ``lookup_min`` still backs the top-N frequency
        filter, which genuinely wants the best rank in any source.
        """
        ranks = [rank for _name, rank in self.lookup_all(term, reading) if rank > 0]
        if not ranks:
            return None
        total = sum(1.0 / rank for rank in ranks)
        return math.floor(len(ranks) / total)

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

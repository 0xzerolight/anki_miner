"""First-hit-wins aggregator over the ordered pitch source chain.

The pitch twin of
:class:`~anki_miner.services.frequency.multi_frequency_service.MultiFrequencyService`,
with deliberately different combination semantics: frequency layers every
source's hit additively into a per-card breakdown, while pitch resolves
FIRST-HIT-WINS — for each provider in chain order, term-scoped
``lookup_entry`` runs (exact pair → single-candidate surface),
and the first non-``None`` result wins. Later sources only fill words earlier
sources miss, which is the point of chaining pitch sources: extra word
coverage, not merged data.

Accepted shadowing tradeoffs (documented, test-pinned, user-reorderable):

* tier-2: a higher source holding a surface under only ONE reading returns it
  for ANY queried reading (the single-candidate fallback is not
  reading-refused), so a lower source's exact ``(surface, reading)`` match for
  a different reading is never consulted. This is the common shadowing case;
  reordering swaps which source shadows.
A tier-aware resolution (running exact-pair lookup across ALL sources before
any unique-surface fallback) was considered and rejected: it lets a lower
source override a higher one, contradicting the reorderable "top source wins"
chain UI.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from anki_miner.services.pitch_accent.provider import IndexedPitchProvider
from anki_miner.services.pitch_accent_service import PitchEntry, PitchMapsStore
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


class MultiPitchAccentService(PitchMapsStore):
    """First-hit-wins pitch lookups over an ordered provider chain.

    Exposes the same public surface as the per-source stores
    (``is_available`` / ``entry_count`` / ``lookup_entry`` / ``lookup`` /
    ``lookup_detailed`` / ``lookup_batch_detailed``), so the episode processor
    and card backfiller consume it unchanged. Inherits the derived lookup API
    from :class:`PitchMapsStore` and overrides only the resolution primitives —
    the base maps stay empty and unused.
    """

    def __init__(self, providers: Sequence[IndexedPitchProvider]):
        super().__init__()
        self._providers: list[IndexedPitchProvider] = list(providers)
        entries = ",".join(f"{provider.display_name}:{provider.entry_count}" for provider in self._providers) or "-"
        log_summary(
            logger,
            "Pitch chain load done",
            sources=len(self._providers),
            entries=entries,
        )

    @property
    def providers(self) -> list[IndexedPitchProvider]:
        return list(self._providers)

    @property
    def entry_count(self) -> int:
        """Total entries across the chain (per-source counts, not deduped)."""
        return sum(p.entry_count for p in self._providers)

    def is_available(self) -> bool:
        return any(p.is_available() for p in self._providers)

    def lookup_entry(self, word: str, reading: str = "") -> PitchEntry | None:
        """First non-None term-scoped resolution across the chain."""
        for provider in self._providers:
            entry = provider.lookup_entry(word, reading)
            if entry is not None:
                return entry
        return None

    def close(self) -> None:
        """No-op, kept for teardown symmetry with the frequency aggregator.

        Providers hold no connections after ``load()`` (see
        :class:`IndexedPitchProvider`), so there is nothing to release.
        """

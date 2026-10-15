"""Known coverage of the active frequency list, by rank band (ST-07).

The list is the first numeric source in the active language's chain: the same
gate as ``MultiFrequencyService.has_numeric_source``, applied to the first
source, because a categorical (JLPT-style) source has no ranks to band.
Bands are rank values, the unit the max-rank mining filter uses, so a user
can read off where to set it next.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anki_miner.config import AnkiMinerConfig

logger = logging.getLogger(__name__)

#: The owner's bands (R11): 1–1k, 1k–2k, 2k–5k, 5k–10k, 10k–20k, as inclusive rank ranges.
RANK_BANDS: tuple[tuple[int, int], ...] = ((1, 1000), (1001, 2000), (2001, 5000), (5001, 10000), (10001, 20000))


@dataclass(frozen=True)
class CoverageBand:
    """One rank band: how many of its words are known, and which are not.

    ``missing`` holds ``(rank, term)`` pairs, best rank first.
    """

    low: int
    high: int
    known: int
    missing: tuple[tuple[int, str], ...]

    @property
    def total(self) -> int:
        return self.known + len(self.missing)

    @property
    def known_share(self) -> float:
        return self.known / self.total if self.total else 0.0


@dataclass(frozen=True)
class FrequencyCoverage:
    """Every band of one frequency list against one known set."""

    source_name: str
    bands: tuple[CoverageBand, ...]


def open_first_numeric_source(config: AnkiMinerConfig) -> IndexedFreqProvider | None:
    """The first numeric source of the active language's chain, loaded; ``None`` without one.

    ``build_sources`` already drops sources stamped for another mining
    language, so a language switch reads that language's list. The caller
    closes the returned provider.
    """
    if not config.frequency_active:
        return None
    registry = FrequencySourceRegistry(config.freqs_root)
    registry.load()
    for provider in registry.build_sources(config):
        if provider.is_categorical:
            continue
        if provider.load():
            return provider
    return None


def _band_index(rank: int) -> int | None:
    for index, (low, high) in enumerate(RANK_BANDS):
        if low <= rank <= high:
            return index
    return None


def measure_coverage(
    provider: IndexedFreqProvider,
    known: set[str],
    fold: Callable[[str], str],
) -> FrequencyCoverage:
    """Count each band's known and missing terms.

    Args:
        provider: A loaded numeric source.
        known: The known set, already in its comparison form.
        fold: Brings a list term into that form. Callers pass
            ``KnownWordDB.normalize_key``, the fold the known-words rows were
            written with.
    """
    known_counts = [0] * len(RANK_BANDS)
    missing: list[list[tuple[int, str]]] = [[] for _ in RANK_BANDS]
    for term, rank in provider.terms_by_rank(RANK_BANDS[-1][1]):
        index = _band_index(rank)
        if index is None:
            continue
        if fold(term) in known:
            known_counts[index] += 1
        else:
            missing[index].append((rank, term))
    bands = tuple(
        CoverageBand(low=low, high=high, known=known_counts[index], missing=tuple(missing[index]))
        for index, (low, high) in enumerate(RANK_BANDS)
    )
    log_summary(
        logger,
        "Frequency coverage measured",
        source=provider.name,
        known=sum(known_counts),
        missing=sum(len(words) for words in missing),
    )
    return FrequencyCoverage(source_name=provider.name, bands=bands)

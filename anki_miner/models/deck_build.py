"""Data models for deck-builder feature."""

from dataclasses import dataclass
from enum import Enum

from anki_miner.utils.file_pairing import FilePair


class DeckSelectionMode(Enum):
    """Determines which lemmas to include in the deck."""

    ALL = "all"
    TOP_N = "top_n"
    COVERAGE_PCT = "coverage_pct"


@dataclass(frozen=True)
class DeckBuildRequest:
    """Request to build a deck from a set of file pairs.

    Attributes:
        pairs: List of video/subtitle file pairs to mine.
        deck_name: Name of the Anki deck to create/add to.
        mode: Selection mode (ALL, TOP_N, COVERAGE_PCT).
        value: Interpreted as N for TOP_N (number of lemmas),
            target percent 0–100 for COVERAGE_PCT, ignored for ALL.
        collection_filter: If True, subtract user's known words
            from candidates; if False, include everything.
    """

    pairs: list[FilePair]
    deck_name: str
    mode: DeckSelectionMode
    value: float
    collection_filter: bool


@dataclass(frozen=True)
class DeckBuildPreview:
    """Preview of what a deck build will produce.

    Attributes:
        total_tokens: Total mineable token occurrences in the corpus.
        unique_lemmas: Distinct mineable lemmas in the corpus.
        candidate_count: Lemmas selected by the chosen mode.
        projected_coverage_pct: Percentage of total tokens the
            candidates cover.
        known_skipped: Candidates already known (count when
            collection_filter is True).
        card_count: Cards that will actually be created.
    """

    total_tokens: int
    unique_lemmas: int
    candidate_count: int
    projected_coverage_pct: float
    known_skipped: int
    card_count: int

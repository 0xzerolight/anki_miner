"""Data models for the deck-builder feature."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DeckSelectionMode(Enum):
    """Determines which lemmas to include in the deck."""

    ALL = "all"
    TOP_N = "top_n"
    COVERAGE_PCT = "coverage_pct"


@dataclass(frozen=True)
class DeckBuildRequest:
    """Request to build a deck from a season's video/subtitle folders.

    Attributes:
        video_folder: Folder containing the season's video files.
        subtitle_folder: Folder containing the season's primary subtitle
            files.
        deck_name: Name of the Anki deck to create/add to; stripped by the
            tab and passed on verbatim.
        skip_known: If True, subtract the user's known words from
            candidates.
        review: Include Review words; frozen at request construction time.
        subtitle_offset: Timing offset in seconds applied to
            ``subtitle_folder``.
        secondary_folder: Optional second subtitle folder (e.g. a dual-sub
            pairing); ``None`` when mining a single subtitle track.
        secondary_offset: Timing offset in seconds applied to
            ``secondary_folder``.
    """

    video_folder: Path
    subtitle_folder: Path
    deck_name: str
    skip_known: bool
    review: bool
    subtitle_offset: float = 0.0
    secondary_folder: Path | None = None
    secondary_offset: float = 0.0


@dataclass(frozen=True)
class DeckCorpus:
    """Aggregated per-season corpus that ranking and preview functions consume.

    Attributes:
        counts: Lemma -> mineable-token occurrence count across all scanned
            episodes.
        row_lemmas: One entry per season-pool row (mined word / card
            candidate), each the set of lemmas that row can card — its own
            lemma plus any sentence-candidate lemmas. See
            :func:`anki_miner.services.corpus_aggregator.row_lemmas`.
        episodes: Number of episodes scanned to build this corpus.
    """

    counts: Mapping[str, int]
    row_lemmas: tuple[frozenset[str], ...]
    episodes: int


@dataclass(frozen=True)
class DeckBuildPreview:
    """Preview of what a deck build will produce.

    Attributes:
        total_tokens: Total mineable token occurrences in the corpus.
        unique_lemmas: Distinct mineable lemmas in the corpus.
        candidate_count: Lemmas selected by the chosen mode.
        projected_coverage_pct: Percentage of total tokens the candidates
            cover.
        known_skipped: Selected lemmas that appear in no corpus row (already
            known/filtered out upstream, or counted but never emitted as a
            row).
        card_count: Corpus rows that will actually be carded.
    """

    total_tokens: int
    unique_lemmas: int
    candidate_count: int
    projected_coverage_pct: float
    known_skipped: int
    card_count: int

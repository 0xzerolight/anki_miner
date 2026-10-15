"""Data models for mining analytics and statistics."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass
class MiningSession:
    """Record of a single episode mining session."""

    id: int | None = None
    series_name: str = ""
    episode_name: str = ""
    total_words: int = 0
    unknown_words: int = 0
    cards_created: int = 0
    elapsed_time: float = 0.0
    mined_at: datetime = field(default_factory=datetime.now)
    #: Write side (AI-16): the Anki note ids this run created and the forms it
    #: wrote to known_words.db as source='mined'. ``record_session`` stores
    #: both; rows read back leave them empty and fill ``note_count`` instead.
    note_ids: list[int] = field(default_factory=list)
    mined_forms: list[str] = field(default_factory=list)
    #: Read side (AI-16): how many note ids are stored for this run. 0 for a
    #: run recorded before AI-16, one that made no cards, or one whose cards
    #: were deleted from Analytics.
    note_count: int = 0
    #: Read side (AI-16, ruling C5): how many of this run's cards were gone
    #: from Anki when Analytics deleted the run and forgot its list. 0 for a
    #: run never deleted there. ``cards_created`` keeps the original count.
    deleted_count: int = 0


@dataclass(frozen=True)
class RunReceipt:
    """What one past run put into Anki and known_words.db (AI-16).

    ``language`` is the run's own mining language, read from its row, so a
    delete after a language switch reverts the right known-words database.
    """

    session_id: int
    language: str
    note_ids: tuple[int, ...]
    mined_forms: tuple[str, ...]


@dataclass
class OverallStats:
    """Overall mining statistics across all sessions."""

    total_sessions: int = 0
    total_cards_created: int = 0
    total_words_encountered: int = 0
    total_unknown_words: int = 0
    total_time_spent: float = 0.0
    series_count: int = 0

    @property
    def avg_cards_per_session(self) -> float:
        """Average cards created per mining session."""
        if self.total_sessions == 0:
            return 0.0
        return self.total_cards_created / self.total_sessions


@dataclass
class DifficultyEntry:
    """Difficulty data for a series (averaged across episodes)."""

    series_name: str = ""
    total_words: int = 0
    unknown_words: int = 0
    difficulty_score: float = 0.0  # 0.0 (easy) to 1.0 (hard)
    recorded_at: datetime = field(default_factory=datetime.now)


class MilestoneKind(Enum):
    """Which counter a milestone tracks.

    The stable half of a milestone. Wording lives in the Analytics tab so it
    goes through ``tr()``; anything spelled out here would ship untranslated.
    """

    CARDS = "cards"
    SESSIONS = "sessions"
    SERIES = "series"


@dataclass
class Milestone:
    """Progress towards one threshold of one counter.

    Carries no prose: the kind plus the numbers are everything the view needs
    to state the fact in the user's language.
    """

    kind: MilestoneKind
    threshold: int = 0
    current_value: int = 0
    achieved: bool = False

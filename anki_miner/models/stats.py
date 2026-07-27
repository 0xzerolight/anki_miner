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
    unique_words: int = 0
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

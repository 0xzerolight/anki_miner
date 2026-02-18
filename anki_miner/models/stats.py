"""Data models for mining analytics and statistics."""

from dataclasses import dataclass, field
from datetime import datetime


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
class SeriesStats:
    """Aggregated statistics for a single series."""

    series_name: str = ""
    episodes_mined: int = 0
    total_words: int = 0
    total_unknown: int = 0
    total_cards_created: int = 0
    total_time: float = 0.0


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
    episode_name: str = ""
    total_words: int = 0
    unknown_words: int = 0
    unique_words: int = 0
    difficulty_score: float = 0.0  # 0.0 (easy) to 1.0 (hard)
    recorded_at: datetime = field(default_factory=datetime.now)


@dataclass
class Milestone:
    """A progress milestone achievement."""

    name: str = ""
    description: str = ""
    threshold: int = 0
    current_value: int = 0
    achieved: bool = False

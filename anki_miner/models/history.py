"""Data model for processing history entries."""

from dataclasses import dataclass, field


@dataclass
class HistoryEntry:
    """A single processing history entry."""

    id: int
    timestamp: str
    video_file: str
    subtitle_file: str
    series_name: str
    cards_created: int
    card_ids: list[int] = field(default_factory=list)
    words_mined: list[str] = field(default_factory=list)
    elapsed_time: float = 0.0

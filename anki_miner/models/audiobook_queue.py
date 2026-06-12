"""Data models for the audiobook file-pair processing queue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AudiobookItemStatus(Enum):
    """Status of a single item in the audiobook processing queue.

    Local file pairs need no probe stage (unlike YouTube URLs), so items
    start out READY.
    """

    READY = "ready"  # added; mineable
    PROCESSING = "processing"  # queue worker handling this item
    COMPLETED = "completed"
    ERROR = "error"  # mining attempt failed


@dataclass(eq=False)  # identity-based equality: list.remove() targets the exact instance
class AudiobookQueueItem:
    """A single audiobook+subtitle file pair in the processing queue."""

    audio_file: Path
    subtitle_file: Path
    status: AudiobookItemStatus = AudiobookItemStatus.READY
    cards_created: int = 0
    error_message: str | None = None


class AudiobookQueue:
    """Manages a queue of audiobook file pairs for sequential batch mining."""

    def __init__(self) -> None:
        """Initialize an empty audiobook queue."""
        self._items: list[AudiobookQueueItem] = []

    def add(self, audio: Path, sub: Path) -> AudiobookQueueItem:
        """Create a new READY item for the given file pair and append it to the queue.

        Args:
            audio: Audiobook audio file to add.
            sub: Matching subtitle file.

        Returns:
            The newly created AudiobookQueueItem.
        """
        item = AudiobookQueueItem(audio_file=audio, subtitle_file=sub)
        self._items.append(item)
        return item

    def remove(self, item: AudiobookQueueItem) -> None:
        """Remove the given item instance from the queue.

        Args:
            item: The exact item object to remove.

        Raises:
            ValueError: If *item* is not present in the queue (mirrors list.remove behaviour).
        """
        self._items.remove(item)

    def all_items(self) -> list[AudiobookQueueItem]:
        """Return a copy of all items in the queue.

        Returns:
            Shallow copy of the internal items list.
        """
        return self._items.copy()

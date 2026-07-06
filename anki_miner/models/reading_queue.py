"""Data models for the reading (manga/novel) processing queue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anki_miner.services.reading.models import ReadingSourceRef


class ReadingItemStatus(Enum):
    """Status of a single item in the reading processing queue.

    Local reading sources need no probe stage (unlike YouTube URLs), so items
    start out READY.
    """

    READY = "ready"  # added; mineable
    PROCESSING = "processing"  # queue worker handling this item
    COMPLETED = "completed"
    ERROR = "error"  # mining attempt failed


@dataclass(eq=False)  # identity-based equality: list.remove() targets the exact instance
class ReadingQueueItem:
    """A single reading source (manga volume or novel file) in the queue."""

    source: ReadingSourceRef
    title: str
    kind: str  # the ref's kind, for row display/grouping
    status: ReadingItemStatus = ReadingItemStatus.READY
    cards_created: int = 0
    error_message: str | None = None


class ReadingQueue:
    """Manages a queue of reading sources for sequential batch mining."""

    def __init__(self) -> None:
        """Initialize an empty reading queue."""
        self._items: list[ReadingQueueItem] = []

    def add(self, source: ReadingSourceRef) -> ReadingQueueItem:
        """Create a new READY item for the given source and append it to the queue.

        Title and kind are derived from *source* for row display.

        Args:
            source: Reading source reference to add.

        Returns:
            The newly created ReadingQueueItem.
        """
        item = ReadingQueueItem(source=source, title=source.title, kind=source.kind)
        self._items.append(item)
        return item

    def remove(self, item: ReadingQueueItem) -> None:
        """Remove the given item instance from the queue.

        Args:
            item: The exact item object to remove.

        Raises:
            ValueError: If *item* is not present in the queue (mirrors list.remove behaviour).
        """
        self._items.remove(item)

    def all_items(self) -> list[ReadingQueueItem]:
        """Return a copy of all items in the queue.

        Returns:
            Shallow copy of the internal items list.
        """
        return self._items.copy()

"""Data models for batch processing queue."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from uuid import uuid4


class QueueItemStatus(Enum):
    """Status of a queue item."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class QueueItem:
    """A single batch processing queue item."""

    anime_folder: Path
    subtitle_folder: Path
    display_name: str  # User-friendly name
    id: str = field(default_factory=lambda: str(uuid4()))
    status: QueueItemStatus = QueueItemStatus.PENDING
    cards_created: int = 0
    error_message: str = ""
    subtitle_offset: float = 0.0  # Per-item subtitle offset in seconds

    @property
    def status_icon(self) -> str:
        """Icon for current status (empty - icons removed)."""
        return {
            QueueItemStatus.PENDING: "",
            QueueItemStatus.PROCESSING: "",
            QueueItemStatus.COMPLETED: "",
            QueueItemStatus.ERROR: "",
        }[self.status]


class BatchQueue:
    """Manages a queue of folder pairs for batch processing."""

    def __init__(self):
        """Initialize an empty batch queue."""
        self._items: list[QueueItem] = []

    def add_item(
        self,
        anime_folder: Path,
        subtitle_folder: Path,
        display_name: str | None = None,
        subtitle_offset: float = 0.0,
    ) -> QueueItem:
        """Add a folder pair to the queue.

        Args:
            anime_folder: Path to anime folder
            subtitle_folder: Path to subtitle folder
            display_name: Optional custom name for this item
            subtitle_offset: Subtitle timing offset in seconds

        Returns:
            The created QueueItem
        """
        if display_name is None:
            display_name = anime_folder.name

        item = QueueItem(
            anime_folder=anime_folder,
            subtitle_folder=subtitle_folder,
            display_name=display_name,
            subtitle_offset=subtitle_offset,
        )
        self._items.append(item)
        return item

    def remove_item(self, item_id: str) -> bool:
        """Remove item from queue by ID.

        Args:
            item_id: ID of the item to remove

        Returns:
            True if item was removed, False if not found
        """
        for i, item in enumerate(self._items):
            if item.id == item_id:
                del self._items[i]
                return True
        return False

    def get_next_pending(self) -> QueueItem | None:
        """Get next pending item in queue.

        Returns:
            Next pending QueueItem, or None if no pending items
        """
        for item in self._items:
            if item.status == QueueItemStatus.PENDING:
                return item
        return None

    def get_all_items(self) -> list[QueueItem]:
        """Get all queue items.

        Returns:
            Copy of the items list
        """
        return self._items.copy()

    def clear(self) -> None:
        """Clear all items from queue."""
        self._items.clear()

    @property
    def total_items(self) -> int:
        """Get total number of items in queue."""
        return len(self._items)

    @property
    def pending_count(self) -> int:
        """Get count of pending items."""
        return sum(1 for item in self._items if item.status == QueueItemStatus.PENDING)

    @property
    def completed_count(self) -> int:
        """Get count of completed items."""
        return sum(1 for item in self._items if item.status == QueueItemStatus.COMPLETED)

    @property
    def total_cards_created(self) -> int:
        """Get total cards created across all items."""
        return sum(item.cards_created for item in self._items)

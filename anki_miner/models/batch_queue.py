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

    video_folder: Path
    subtitle_folder: Path
    display_name: str  # User-friendly name
    id: str = field(default_factory=lambda: str(uuid4()))
    status: QueueItemStatus = QueueItemStatus.PENDING
    cards_created: int = 0
    error_message: str = ""
    subtitle_offset: float = 0.0  # Per-item subtitle offset in seconds
    retry_count: int = 0
    max_retries: int = 2
    committed_pair_keys: set[tuple[Path, Path]] = field(default_factory=set)


class BatchQueue:
    """Manages a queue of folder pairs for batch processing."""

    def __init__(self):
        """Initialize an empty batch queue."""
        self._items: list[QueueItem] = []

    def add_item(
        self,
        video_folder: Path,
        subtitle_folder: Path,
        display_name: str | None = None,
        subtitle_offset: float = 0.0,
    ) -> QueueItem:
        """Add a folder pair to the queue.

        Args:
            video_folder: Path to video folder
            subtitle_folder: Path to subtitle folder
            display_name: Optional custom name for this item
            subtitle_offset: Subtitle timing offset in seconds

        Returns:
            The created QueueItem
        """
        if display_name is None:
            display_name = video_folder.name

        item = QueueItem(
            video_folder=video_folder,
            subtitle_folder=subtitle_folder,
            display_name=display_name,
            subtitle_offset=subtitle_offset,
        )
        self._items.append(item)
        return item

    def get_next_pending(self) -> QueueItem | None:
        """Get next pending item in queue.

        Called from the worker thread, which also writes item status
        synchronously at pick/finish time (see BatchQueueWorkerThread.run), so
        an in-flight item is never returned twice. GUI code must not write
        item status while a run is active.

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
    def failed_count(self) -> int:
        """Get count of failed items."""
        return sum(1 for item in self._items if item.status == QueueItemStatus.ERROR)

    def reset_failed_for_retry(self) -> int:
        """Reset failed items to PENDING for retry.

        Only resets items that haven't exceeded their max_retries.

        Returns:
            Number of items reset for retry
        """
        reset_count = 0
        for item in self._items:
            if item.status == QueueItemStatus.ERROR and item.retry_count < item.max_retries:
                item.status = QueueItemStatus.PENDING
                item.retry_count += 1
                item.error_message = ""
                reset_count += 1
        return reset_count

    @property
    def total_cards_created(self) -> int:
        """Get total cards created across all items."""
        return sum(item.cards_created for item in self._items)

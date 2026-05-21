"""Data models for the YouTube multi-URL processing queue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anki_miner.models.youtube import SubMode, VideoInfo


class YouTubeItemStatus(Enum):
    """Status of a single item in the YouTube processing queue."""

    PENDING = "pending"  # added, probe not yet started
    PROBING = "probing"  # YouTubeProbeWorker in flight
    READY = "ready"  # probe succeeded; mineable
    PROBE_ERROR = "probe_error"  # probe failed; not mineable
    PROCESSING = "processing"  # queue worker handling this item
    COMPLETED = "completed"
    ERROR = "error"  # mining attempt failed


@dataclass
class YouTubeQueueItem:
    """A single item in the YouTube URL processing queue."""

    url: str
    status: YouTubeItemStatus
    video_id: str | None = None
    video_info: VideoInfo | None = None
    resolved_sub_mode: SubMode | None = None
    cards_created: int = 0
    error_message: str | None = None
    retry_count: int = 0  # 0 or 1


class YouTubeQueue:
    """Manages a queue of YouTube URLs for sequential batch mining."""

    def __init__(self) -> None:
        """Initialize an empty YouTube queue."""
        self._items: list[YouTubeQueueItem] = []

    def add(self, url: str) -> YouTubeQueueItem:
        """Create a new PENDING item for the given URL and append it to the queue.

        Args:
            url: YouTube URL to add.

        Returns:
            The newly created YouTubeQueueItem.
        """
        item = YouTubeQueueItem(url=url, status=YouTubeItemStatus.PENDING)
        self._items.append(item)
        return item

    def remove(self, item: YouTubeQueueItem) -> None:
        """Remove the given item instance from the queue.

        Args:
            item: The exact item object to remove.

        Raises:
            ValueError: If *item* is not present in the queue (mirrors list.remove behaviour).
        """
        self._items.remove(item)

    def clear_non_processing(self) -> None:
        """Remove every item whose status is not PROCESSING.

        Items currently being processed are preserved so an in-flight worker
        can finish without its item disappearing under it.
        """
        self._items = [i for i in self._items if i.status == YouTubeItemStatus.PROCESSING]

    def pending_ready_items(self) -> list[YouTubeQueueItem]:
        """Return items eligible for a worker run (PENDING or READY), in queue order.

        Returns:
            A new list containing only PENDING and READY items, preserving
            insertion order.
        """
        return [i for i in self._items if i.status in (YouTubeItemStatus.PENDING, YouTubeItemStatus.READY)]

    def all_items(self) -> list[YouTubeQueueItem]:
        """Return a copy of all items in the queue.

        Returns:
            Shallow copy of the internal items list.
        """
        return self._items.copy()

    def reset_errors_to_pending(self) -> None:
        """Reset every ERROR item back to PENDING for a re-run.

        Clears ``error_message`` and resets ``retry_count`` to 0.
        Non-ERROR items are not modified.
        """
        for item in self._items:
            if item.status == YouTubeItemStatus.ERROR:
                item.status = YouTubeItemStatus.PENDING
                item.error_message = None
                item.retry_count = 0

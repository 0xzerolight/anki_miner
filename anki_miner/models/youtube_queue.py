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


@dataclass(eq=False)  # identity-based equality: list.remove() targets the exact instance
class YouTubeQueueItem:
    """A single item in the YouTube URL processing queue."""

    url: str
    status: YouTubeItemStatus
    video_id: str | None = None
    video_info: VideoInfo | None = None
    resolved_sub_mode: SubMode | None = None
    cards_created: int = 0
    error_message: str | None = None
    retry_count: int = 0  # incremented by worker; capped at 1 (single retry only)
    display_title: str | None = None  # shown while status is PROBING (set by playlist expansion)


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

    def all_items(self) -> list[YouTubeQueueItem]:
        """Return a copy of all items in the queue.

        Returns:
            Shallow copy of the internal items list.
        """
        return self._items.copy()

"""Data models for the YouTube multi-URL processing queue."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from anki_miner.models.mining_queue import MiningQueue
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
    display_title: str | None = None  # shown while status is PROBING (set by playlist expansion)
    #: Stable identity that survives quitting (D16-C). Runtime identity is still
    #: the object (``eq=False``); this is what a restored snapshot re-attaches so
    #: a row keeps its place across a restart. ``video_info`` deliberately does
    #: NOT survive — it is probe output over a workspace that may be gone.
    item_id: str = field(default_factory=lambda: str(uuid4()))


class YouTubeQueue(MiningQueue[YouTubeQueueItem]):
    """Manages a queue of YouTube URLs for sequential batch mining."""

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

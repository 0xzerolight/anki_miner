"""Data models for reading (manga/novel) mining.

Each Mine run builds an ephemeral :class:`ReadingQueueItem` per source
(one manga volume or one novel file) and hands the list to the reading queue
worker; there is no persistent queue collection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anki_miner.models.reading import ReadingSourceRef


class ReadingItemStatus(Enum):
    """Status of a single reading source as the worker mines it.

    Local reading sources need no probe stage (unlike YouTube URLs), so items
    start out READY.
    """

    READY = "ready"  # added; mineable
    PROCESSING = "processing"  # queue worker handling this item
    COMPLETED = "completed"
    ERROR = "error"  # mining attempt failed


@dataclass(eq=False)  # identity-based equality: idx signals target the exact instance
class ReadingQueueItem:
    """A single reading source (manga volume or novel file) being mined."""

    source: ReadingSourceRef
    title: str
    kind: str  # the ref's kind, for display/grouping
    status: ReadingItemStatus = ReadingItemStatus.READY
    cards_created: int = 0
    error_message: str | None = None

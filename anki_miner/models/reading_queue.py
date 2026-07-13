"""Data models for reading (manga/novel) mining.

Each Mine run builds an ephemeral :class:`ReadingQueueItem` per source
(one manga volume or one novel file) and hands the list to the reading queue
worker; there is no persistent queue collection.
"""

from __future__ import annotations

from dataclasses import dataclass

from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import ReadingSourceRef


@dataclass(eq=False)  # identity-based equality: idx signals target the exact instance
class ReadingQueueItem:
    """A single reading source (manga volume or novel file) being mined.

    Local reading sources need no probe stage (unlike YouTube URLs), so items
    start out READY.
    """

    source: ReadingSourceRef
    title: str
    kind: str  # the ref's kind, for display/grouping
    status: ReadyItemStatus = ReadyItemStatus.READY
    cards_created: int = 0
    error_message: str | None = None

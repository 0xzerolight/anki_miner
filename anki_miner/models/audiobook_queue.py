"""Data models for the audiobook file-pair processing queue."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from anki_miner.models.mining_queue import MiningQueue, ReadyItemStatus


@dataclass(eq=False)  # identity-based equality: list.remove() targets the exact instance
class AudiobookQueueItem:
    """A single audiobook+subtitle file pair in the processing queue.

    Local file pairs need no probe stage (unlike YouTube URLs), so items
    start out READY.
    """

    audio_file: Path
    subtitle_file: Path
    status: ReadyItemStatus = ReadyItemStatus.READY
    cards_created: int = 0
    error_message: str | None = None
    #: Stable identity that survives quitting (D16-C). Runtime identity is still
    #: the object (``eq=False``); this is what a restored snapshot re-attaches so
    #: a row keeps its place and its history across a restart.
    item_id: str = field(default_factory=lambda: str(uuid4()))


class AudiobookQueue(MiningQueue[AudiobookQueueItem]):
    """Manages a queue of audiobook file pairs for sequential batch mining."""

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

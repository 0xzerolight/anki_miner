"""Shared models for READY-family mining queues (audiobook, reading).

Local media (audiobook file pairs, reading sources) need no probe stage, so
their queue items share a single 4-value status lifecycle and an identical
collection surface. YouTube's richer probe lifecycle lives separately in
:class:`~anki_miner.models.youtube_queue.YouTubeItemStatus`, but its queue
still reuses :class:`MiningQueue` for storage.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Generic, TypeVar


class ReadyItemStatus(Enum):
    """Status of a queue item that needs no probe stage.

    Local file pairs and reading sources are mineable the moment they are
    added (unlike YouTube URLs, which must be probed first), so items start
    out READY.
    """

    READY = "ready"  # added; mineable
    PROCESSING = "processing"  # queue worker handling this item
    COMPLETED = "completed"
    ERROR = "error"  # mining attempt failed


ItemT = TypeVar("ItemT")


class MiningQueue(Generic[ItemT]):
    """Ordered collection of queue items with identity-based removal.

    Subclasses supply a type-specific ``add`` factory; the shared removal and
    snapshot operations live here. Items use identity-based equality
    (``@dataclass(eq=False)``) so :meth:`remove` targets the exact instance.
    """

    def __init__(self) -> None:
        """Initialize an empty queue."""
        self._items: list[ItemT] = []

    def remove(self, item: ItemT) -> None:
        """Remove the given item instance from the queue.

        Args:
            item: The exact item object to remove.

        Raises:
            ValueError: If *item* is not present in the queue (mirrors
                list.remove behaviour).
        """
        self._items.remove(item)

    def all_items(self) -> list[ItemT]:
        """Return a copy of all items in the queue.

        Returns:
            Shallow copy of the internal items list.
        """
        return self._items.copy()

    def reorder(self, order: Sequence[ItemT]) -> None:
        """Adopt *order* as the queue's new order.

        The queue is what the run reads its items from, so a reorder that added,
        dropped or duplicated a row would leave the tab's row map and the frozen
        ``_run_items`` snapshot describing a queue that no longer exists. Only an
        exact permutation of the current contents is accepted; anything else is
        refused and the existing order is left untouched.

        Membership is checked by identity, not equality: queue items are
        ``eq=False`` dataclasses, and two rows built from the same file pair are
        two distinct rows.

        Args:
            order: Every current item, in the order the queue should adopt.

        Raises:
            ValueError: If *order* is not a permutation of the current items.
        """
        if len(order) != len(self._items):
            raise ValueError("reorder() needs exactly the queue's current items")

        current = {id(item) for item in self._items}
        seen: set[int] = set()
        for item in order:
            key = id(item)
            if key not in current or key in seen:
                raise ValueError("reorder() needs a permutation of the queue's current items")
            seen.add(key)

        self._items = list(order)

"""Tests for :meth:`MiningQueue.reorder` (D28).

The queue list became user-reorderable, so the model needs one validated way to
adopt a new order. It refuses anything that is not a permutation of what it
already holds: a reorder that silently added, dropped or duplicated an item
would desynchronise the row map and the frozen run snapshot from the queue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.models.audiobook_queue import AudiobookQueue
from anki_miner.models.youtube_queue import YouTubeQueue, YouTubeQueueItem


def _audiobook_queue(n: int) -> tuple[AudiobookQueue, list]:
    queue = AudiobookQueue()
    items = [queue.add(Path(f"/tmp/a{i}.m4b"), Path(f"/tmp/a{i}.srt")) for i in range(n)]
    return queue, items


def test_reorder_adopts_the_given_permutation() -> None:
    queue, items = _audiobook_queue(3)

    queue.reorder([items[2], items[0], items[1]])

    assert queue.all_items() == [items[2], items[0], items[1]]


def test_reorder_of_an_empty_queue_is_a_no_op() -> None:
    queue = AudiobookQueue()

    queue.reorder([])

    assert queue.all_items() == []


def test_reorder_rejects_a_short_order() -> None:
    queue, items = _audiobook_queue(3)

    with pytest.raises(ValueError):
        queue.reorder([items[0], items[1]])

    assert queue.all_items() == items


def test_reorder_rejects_a_duplicated_item() -> None:
    queue, items = _audiobook_queue(3)

    with pytest.raises(ValueError):
        queue.reorder([items[0], items[0], items[1]])

    assert queue.all_items() == items


def test_reorder_rejects_a_foreign_item() -> None:
    queue, items = _audiobook_queue(2)
    foreign = YouTubeQueueItem(url="https://youtu.be/x", status=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        queue.reorder([items[0], foreign])

    assert queue.all_items() == items


def test_reorder_uses_identity_not_equality() -> None:
    """Two equal-looking items are distinct rows; reorder must keep both."""
    queue = AudiobookQueue()
    first = queue.add(Path("/tmp/same.m4b"), Path("/tmp/same.srt"))
    second = queue.add(Path("/tmp/same.m4b"), Path("/tmp/same.srt"))

    queue.reorder([second, first])

    ordered = queue.all_items()
    assert ordered[0] is second
    assert ordered[1] is first


def test_youtube_queue_reorders_too() -> None:
    queue = YouTubeQueue()
    a = queue.add("https://youtu.be/a")
    b = queue.add("https://youtu.be/b")

    queue.reorder([b, a])

    assert queue.all_items() == [b, a]

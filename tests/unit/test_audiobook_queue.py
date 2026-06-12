"""Tests for audiobook_queue module."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.models.audiobook_queue import (
    AudiobookItemStatus,
    AudiobookQueue,
    AudiobookQueueItem,
)

AUDIO = Path("/books/show/episode_01.mp3")
SUB = Path("/books/show/episode_01.srt")

# ---------------------------------------------------------------------------
# AudiobookQueueItem defaults
# ---------------------------------------------------------------------------


class TestAudiobookQueueItemDefaults:
    """AudiobookQueueItem construction with only required fields."""

    def test_default_optional_fields(self):
        item = AudiobookQueueItem(audio_file=AUDIO, subtitle_file=SUB)
        assert item.status == AudiobookItemStatus.READY
        assert item.cards_created == 0
        assert item.error_message is None

    def test_files_set(self):
        item = AudiobookQueueItem(audio_file=AUDIO, subtitle_file=SUB)
        assert item.audio_file == AUDIO
        assert item.subtitle_file == SUB

    def test_status_can_be_set_at_construction(self):
        item = AudiobookQueueItem(
            audio_file=AUDIO,
            subtitle_file=SUB,
            status=AudiobookItemStatus.PROCESSING,
        )
        assert item.status == AudiobookItemStatus.PROCESSING


# ---------------------------------------------------------------------------
# AudiobookItemStatus
# ---------------------------------------------------------------------------


class TestAudiobookItemStatus:
    """No probe stage for local files, so the status set is smaller."""

    def test_status_members(self):
        assert {s.name for s in AudiobookItemStatus} == {
            "READY",
            "PROCESSING",
            "COMPLETED",
            "ERROR",
        }


# ---------------------------------------------------------------------------
# AudiobookQueue.add
# ---------------------------------------------------------------------------


class TestAudiobookQueueAdd:
    """Tests for AudiobookQueue.add."""

    def test_add_returns_ready_item(self):
        queue = AudiobookQueue()
        item = queue.add(AUDIO, SUB)
        assert item.status == AudiobookItemStatus.READY

    def test_add_sets_files(self):
        queue = AudiobookQueue()
        item = queue.add(AUDIO, SUB)
        assert item.audio_file == AUDIO
        assert item.subtitle_file == SUB

    def test_add_appends_in_order(self):
        queue = AudiobookQueue()
        pairs = [
            (Path("/books/a.mp3"), Path("/books/a.srt")),
            (Path("/books/b.mp3"), Path("/books/b.srt")),
            (Path("/books/c.mp3"), Path("/books/c.srt")),
        ]
        for audio, sub in pairs:
            queue.add(audio, sub)
        all_items = queue.all_items()
        assert [(i.audio_file, i.subtitle_file) for i in all_items] == pairs

    def test_add_returns_item_in_all_items(self):
        queue = AudiobookQueue()
        item = queue.add(AUDIO, SUB)
        assert item in queue.all_items()

    def test_add_multiple_returns_distinct_objects(self):
        queue = AudiobookQueue()
        item1 = queue.add(AUDIO, SUB)
        item2 = queue.add(AUDIO, SUB)
        assert item1 is not item2


# ---------------------------------------------------------------------------
# AudiobookQueue.all_items
# ---------------------------------------------------------------------------


class TestAudiobookQueueAllItems:
    """Tests for AudiobookQueue.all_items."""

    def test_all_items_returns_copy(self):
        queue = AudiobookQueue()
        queue.add(AUDIO, SUB)
        snapshot = queue.all_items()
        snapshot.clear()
        assert len(queue.all_items()) == 1  # original not affected

    def test_all_items_empty_on_new_queue(self):
        queue = AudiobookQueue()
        assert queue.all_items() == []


# ---------------------------------------------------------------------------
# AudiobookQueue.remove
# ---------------------------------------------------------------------------


class TestAudiobookQueueRemove:
    """Tests for AudiobookQueue.remove."""

    def test_remove_drops_item(self):
        queue = AudiobookQueue()
        item = queue.add(AUDIO, SUB)
        queue.remove(item)
        assert item not in queue.all_items()

    def test_remove_decrements_length(self):
        queue = AudiobookQueue()
        item1 = queue.add(AUDIO, SUB)
        queue.add(Path("/books/b.mp3"), Path("/books/b.srt"))
        queue.remove(item1)
        assert len(queue.all_items()) == 1

    def test_remove_leaves_other_items(self):
        queue = AudiobookQueue()
        item1 = queue.add(AUDIO, SUB)
        item2 = queue.add(Path("/books/b.mp3"), Path("/books/b.srt"))
        queue.remove(item1)
        assert item2 in queue.all_items()

    def test_remove_non_member_raises_value_error(self):
        queue = AudiobookQueue()
        orphan = AudiobookQueueItem(audio_file=AUDIO, subtitle_file=SUB)
        with pytest.raises(ValueError):
            queue.remove(orphan)

    def test_remove_already_removed_raises_value_error(self):
        queue = AudiobookQueue()
        item = queue.add(AUDIO, SUB)
        queue.remove(item)
        with pytest.raises(ValueError):
            queue.remove(item)

    def test_remove_uses_identity_not_equality(self):
        """remove() must remove the specific instance, not the first field-equal item."""
        queue = AudiobookQueue()
        item_a = queue.add(AUDIO, SUB)
        item_b = queue.add(AUDIO, SUB)
        queue.remove(item_b)
        assert len(queue.all_items()) == 1
        assert queue.all_items()[0] is item_a

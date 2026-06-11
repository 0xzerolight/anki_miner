"""Tests for youtube_queue module."""

from __future__ import annotations

import pytest

from anki_miner.models.youtube_queue import (
    YouTubeItemStatus,
    YouTubeQueue,
    YouTubeQueueItem,
)

# ---------------------------------------------------------------------------
# YouTubeQueueItem defaults
# ---------------------------------------------------------------------------


class TestYouTubeQueueItemDefaults:
    """YouTubeQueueItem construction with only required fields."""

    def test_default_optional_fields(self):
        item = YouTubeQueueItem(url="https://youtu.be/abc123", status=YouTubeItemStatus.PENDING)
        assert item.video_id is None
        assert item.video_info is None
        assert item.resolved_sub_mode is None
        assert item.cards_created == 0
        assert item.error_message is None
        assert item.retry_count == 0

    def test_url_and_status_set(self):
        item = YouTubeQueueItem(url="https://youtu.be/xyz", status=YouTubeItemStatus.READY)
        assert item.url == "https://youtu.be/xyz"
        assert item.status == YouTubeItemStatus.READY

    def test_display_title_defaults_to_none(self):
        item = YouTubeQueueItem(url="https://youtu.be/abc123", status=YouTubeItemStatus.PENDING)
        assert item.display_title is None

    def test_display_title_settable(self):
        item = YouTubeQueueItem(url="https://youtu.be/abc123", status=YouTubeItemStatus.PROBING)
        item.display_title = "My Video Title"
        assert item.display_title == "My Video Title"

    def test_display_title_can_be_set_at_construction(self):
        item = YouTubeQueueItem(
            url="https://youtu.be/abc123",
            status=YouTubeItemStatus.PROBING,
            display_title="Preset Title",
        )
        assert item.display_title == "Preset Title"


# ---------------------------------------------------------------------------
# YouTubeQueue.add
# ---------------------------------------------------------------------------


class TestYouTubeQueueAdd:
    """Tests for YouTubeQueue.add."""

    def test_add_returns_pending_item(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        assert item.status == YouTubeItemStatus.PENDING

    def test_add_sets_url(self):
        queue = YouTubeQueue()
        url = "https://www.youtube.com/watch?v=def456"
        item = queue.add(url)
        assert item.url == url

    def test_add_appends_in_order(self):
        queue = YouTubeQueue()
        url1 = "https://youtu.be/aaa"
        url2 = "https://youtu.be/bbb"
        url3 = "https://youtu.be/ccc"
        queue.add(url1)
        queue.add(url2)
        queue.add(url3)
        all_items = queue.all_items()
        assert [i.url for i in all_items] == [url1, url2, url3]

    def test_add_returns_item_in_all_items(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        assert item in queue.all_items()

    def test_add_multiple_returns_distinct_objects(self):
        queue = YouTubeQueue()
        item1 = queue.add("https://youtu.be/aaa")
        item2 = queue.add("https://youtu.be/bbb")
        assert item1 is not item2


# ---------------------------------------------------------------------------
# YouTubeQueue.all_items
# ---------------------------------------------------------------------------


class TestYouTubeQueueAllItems:
    """Tests for YouTubeQueue.all_items."""

    def test_all_items_returns_copy(self):
        queue = YouTubeQueue()
        queue.add("https://youtu.be/aaa")
        snapshot = queue.all_items()
        snapshot.clear()
        assert len(queue.all_items()) == 1  # original not affected

    def test_all_items_empty_on_new_queue(self):
        queue = YouTubeQueue()
        assert queue.all_items() == []


# ---------------------------------------------------------------------------
# YouTubeQueue.remove
# ---------------------------------------------------------------------------


class TestYouTubeQueueRemove:
    """Tests for YouTubeQueue.remove."""

    def test_remove_drops_item(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        queue.remove(item)
        assert item not in queue.all_items()

    def test_remove_decrements_length(self):
        queue = YouTubeQueue()
        item1 = queue.add("https://youtu.be/aaa")
        queue.add("https://youtu.be/bbb")
        queue.remove(item1)
        assert len(queue.all_items()) == 1

    def test_remove_leaves_other_items(self):
        queue = YouTubeQueue()
        item1 = queue.add("https://youtu.be/aaa")
        item2 = queue.add("https://youtu.be/bbb")
        queue.remove(item1)
        assert item2 in queue.all_items()

    def test_remove_non_member_raises_value_error(self):
        queue = YouTubeQueue()
        orphan = YouTubeQueueItem(url="https://youtu.be/orphan", status=YouTubeItemStatus.PENDING)
        with pytest.raises(ValueError):
            queue.remove(orphan)

    def test_remove_already_removed_raises_value_error(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        queue.remove(item)
        with pytest.raises(ValueError):
            queue.remove(item)

    def test_remove_uses_identity_not_equality(self):
        """remove() must remove the specific instance, not the first field-equal item."""
        queue = YouTubeQueue()
        item_a = queue.add("https://youtu.be/abc")
        item_b = queue.add("https://youtu.be/abc")
        queue.remove(item_b)
        assert len(queue.all_items()) == 1
        assert queue.all_items()[0] is item_a

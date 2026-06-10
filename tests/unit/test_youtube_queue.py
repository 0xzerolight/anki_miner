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


# ---------------------------------------------------------------------------
# YouTubeQueue.clear_non_processing
# ---------------------------------------------------------------------------


class TestYouTubeQueueClearNonProcessing:
    """Tests for YouTubeQueue.clear_non_processing."""

    def test_clears_all_statuses_except_processing(self):
        queue = YouTubeQueue()
        pending = queue.add("https://youtu.be/pending")
        processing = queue.add("https://youtu.be/processing")
        completed = queue.add("https://youtu.be/completed")
        error = queue.add("https://youtu.be/error")
        ready = queue.add("https://youtu.be/ready")

        pending.status = YouTubeItemStatus.PENDING
        processing.status = YouTubeItemStatus.PROCESSING
        completed.status = YouTubeItemStatus.COMPLETED
        error.status = YouTubeItemStatus.ERROR
        ready.status = YouTubeItemStatus.READY

        queue.clear_non_processing()

        remaining = queue.all_items()
        assert remaining == [processing]

    def test_clear_non_processing_removes_probing(self):
        queue = YouTubeQueue()
        probing = queue.add("https://youtu.be/probing")
        probing.status = YouTubeItemStatus.PROBING
        queue.clear_non_processing()
        assert queue.all_items() == []

    def test_clear_non_processing_removes_probe_error(self):
        queue = YouTubeQueue()
        probe_error = queue.add("https://youtu.be/probe_error")
        probe_error.status = YouTubeItemStatus.PROBE_ERROR
        queue.clear_non_processing()
        assert queue.all_items() == []

    def test_clear_non_processing_empty_queue_is_noop(self):
        queue = YouTubeQueue()
        queue.clear_non_processing()  # should not raise
        assert queue.all_items() == []

    def test_clear_non_processing_all_processing_untouched(self):
        queue = YouTubeQueue()
        item1 = queue.add("https://youtu.be/aaa")
        item2 = queue.add("https://youtu.be/bbb")
        item1.status = YouTubeItemStatus.PROCESSING
        item2.status = YouTubeItemStatus.PROCESSING
        queue.clear_non_processing()
        assert len(queue.all_items()) == 2


# ---------------------------------------------------------------------------
# YouTubeQueue.pending_ready_items
# ---------------------------------------------------------------------------


class TestYouTubeQueuePendingReadyItems:
    """Tests for YouTubeQueue.pending_ready_items."""

    def test_returns_only_pending_and_ready(self):
        queue = YouTubeQueue()
        pending = queue.add("https://youtu.be/pending")
        ready = queue.add("https://youtu.be/ready")
        probing = queue.add("https://youtu.be/probing")
        probe_error = queue.add("https://youtu.be/probe_error")
        completed = queue.add("https://youtu.be/completed")
        processing = queue.add("https://youtu.be/processing")
        error = queue.add("https://youtu.be/error")

        pending.status = YouTubeItemStatus.PENDING
        ready.status = YouTubeItemStatus.READY
        probing.status = YouTubeItemStatus.PROBING
        probe_error.status = YouTubeItemStatus.PROBE_ERROR
        completed.status = YouTubeItemStatus.COMPLETED
        processing.status = YouTubeItemStatus.PROCESSING
        error.status = YouTubeItemStatus.ERROR

        result = queue.pending_ready_items()
        assert result == [pending, ready]

    def test_filters_probe_error_probing_completed(self):
        queue = YouTubeQueue()
        for status in (YouTubeItemStatus.PROBE_ERROR, YouTubeItemStatus.PROBING, YouTubeItemStatus.COMPLETED):
            item = queue.add(f"https://youtu.be/{status.value}")
            item.status = status
        assert queue.pending_ready_items() == []

    def test_filters_processing_and_error(self):
        queue = YouTubeQueue()
        for status in (YouTubeItemStatus.PROCESSING, YouTubeItemStatus.ERROR):
            item = queue.add(f"https://youtu.be/{status.value}")
            item.status = status
        assert queue.pending_ready_items() == []

    def test_preserves_queue_order(self):
        queue = YouTubeQueue()
        # interleave READY and PENDING with other statuses in between
        r1 = queue.add("https://youtu.be/r1")
        p1 = queue.add("https://youtu.be/p1")
        done = queue.add("https://youtu.be/done")
        r2 = queue.add("https://youtu.be/r2")
        p2 = queue.add("https://youtu.be/p2")

        r1.status = YouTubeItemStatus.READY
        p1.status = YouTubeItemStatus.PENDING
        done.status = YouTubeItemStatus.COMPLETED
        r2.status = YouTubeItemStatus.READY
        p2.status = YouTubeItemStatus.PENDING

        result = queue.pending_ready_items()
        assert result == [r1, p1, r2, p2]

    def test_returns_copy(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        item.status = YouTubeItemStatus.PENDING
        snapshot = queue.pending_ready_items()
        snapshot.clear()
        assert len(queue.pending_ready_items()) == 1


# ---------------------------------------------------------------------------
# YouTubeQueue.reset_errors_to_pending
# ---------------------------------------------------------------------------


class TestYouTubeQueueResetErrorsToPending:
    """Tests for YouTubeQueue.reset_errors_to_pending."""

    def test_error_items_become_pending(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        item.status = YouTubeItemStatus.ERROR
        item.error_message = "some error"
        item.retry_count = 1

        queue.reset_errors_to_pending()

        assert item.status == YouTubeItemStatus.PENDING

    def test_error_message_cleared(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        item.status = YouTubeItemStatus.ERROR
        item.error_message = "some error"

        queue.reset_errors_to_pending()

        assert item.error_message is None

    def test_retry_count_reset_to_zero(self):
        queue = YouTubeQueue()
        item = queue.add("https://youtu.be/abc")
        item.status = YouTubeItemStatus.ERROR
        item.retry_count = 1

        queue.reset_errors_to_pending()

        assert item.retry_count == 0

    def test_non_error_items_untouched(self):
        queue = YouTubeQueue()
        pending = queue.add("https://youtu.be/pending")
        ready = queue.add("https://youtu.be/ready")
        completed = queue.add("https://youtu.be/completed")
        processing = queue.add("https://youtu.be/processing")
        probing = queue.add("https://youtu.be/probing")
        probe_error = queue.add("https://youtu.be/probe_error")

        pending.status = YouTubeItemStatus.PENDING
        ready.status = YouTubeItemStatus.READY
        completed.status = YouTubeItemStatus.COMPLETED
        processing.status = YouTubeItemStatus.PROCESSING
        probing.status = YouTubeItemStatus.PROBING
        probe_error.status = YouTubeItemStatus.PROBE_ERROR

        queue.reset_errors_to_pending()

        assert pending.status == YouTubeItemStatus.PENDING
        assert ready.status == YouTubeItemStatus.READY
        assert completed.status == YouTubeItemStatus.COMPLETED
        assert processing.status == YouTubeItemStatus.PROCESSING
        assert probing.status == YouTubeItemStatus.PROBING
        assert probe_error.status == YouTubeItemStatus.PROBE_ERROR

    def test_multiple_error_items_all_reset(self):
        queue = YouTubeQueue()
        items = []
        for i in range(3):
            item = queue.add(f"https://youtu.be/err{i}")
            item.status = YouTubeItemStatus.ERROR
            item.error_message = f"error {i}"
            item.retry_count = 1
            items.append(item)

        queue.reset_errors_to_pending()

        for item in items:
            assert item.status == YouTubeItemStatus.PENDING
            assert item.error_message is None
            assert item.retry_count == 0

    def test_empty_queue_is_noop(self):
        queue = YouTubeQueue()
        queue.reset_errors_to_pending()  # should not raise

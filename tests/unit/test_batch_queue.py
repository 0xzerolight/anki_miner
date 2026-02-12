"""Tests for batch_queue module."""

from anki_miner.models.batch_queue import BatchQueue, QueueItem, QueueItemStatus


class TestQueueItem:
    """Tests for QueueItem dataclass."""

    def test_default_status_is_pending(self, tmp_path):
        item = QueueItem(
            anime_folder=tmp_path / "anime",
            subtitle_folder=tmp_path / "subs",
            display_name="Test Anime",
        )
        assert item.status == QueueItemStatus.PENDING

    def test_auto_generated_id(self, tmp_path):
        item1 = QueueItem(anime_folder=tmp_path, subtitle_folder=tmp_path, display_name="A")
        item2 = QueueItem(anime_folder=tmp_path, subtitle_folder=tmp_path, display_name="B")
        assert item1.id != item2.id
        assert len(item1.id) > 0

    def test_custom_offset(self, tmp_path):
        item = QueueItem(
            anime_folder=tmp_path,
            subtitle_folder=tmp_path,
            display_name="Test",
            subtitle_offset=2.5,
        )
        assert item.subtitle_offset == 2.5

    def test_default_offset_is_zero(self, tmp_path):
        item = QueueItem(anime_folder=tmp_path, subtitle_folder=tmp_path, display_name="Test")
        assert item.subtitle_offset == 0.0

    def test_default_cards_created_is_zero(self, tmp_path):
        item = QueueItem(anime_folder=tmp_path, subtitle_folder=tmp_path, display_name="Test")
        assert item.cards_created == 0

    def test_default_error_message_empty(self, tmp_path):
        item = QueueItem(anime_folder=tmp_path, subtitle_folder=tmp_path, display_name="Test")
        assert item.error_message == ""

    def test_status_icon_returns_string(self, tmp_path):
        item = QueueItem(anime_folder=tmp_path, subtitle_folder=tmp_path, display_name="Test")
        for status in QueueItemStatus:
            item.status = status
            assert isinstance(item.status_icon, str)


class TestBatchQueue:
    """Tests for BatchQueue class."""

    def test_starts_empty(self):
        queue = BatchQueue()
        assert queue.total_items == 0

    def test_add_item(self, tmp_path):
        queue = BatchQueue()
        item = queue.add_item(tmp_path / "anime", tmp_path / "subs", "My Anime")
        assert queue.total_items == 1
        assert item.display_name == "My Anime"
        assert item.anime_folder == tmp_path / "anime"

    def test_add_item_default_display_name(self, tmp_path):
        queue = BatchQueue()
        anime_folder = tmp_path / "Naruto"
        anime_folder.mkdir()
        item = queue.add_item(anime_folder, tmp_path / "subs")
        assert item.display_name == "Naruto"

    def test_add_item_with_offset(self, tmp_path):
        queue = BatchQueue()
        item = queue.add_item(tmp_path / "anime", tmp_path / "subs", "Test", subtitle_offset=1.5)
        assert item.subtitle_offset == 1.5

    def test_remove_item_success(self, tmp_path):
        queue = BatchQueue()
        item = queue.add_item(tmp_path / "anime", tmp_path / "subs", "Test")
        assert queue.remove_item(item.id) is True
        assert queue.total_items == 0

    def test_remove_item_not_found(self):
        queue = BatchQueue()
        assert queue.remove_item("nonexistent-id") is False

    def test_get_next_pending(self, tmp_path):
        queue = BatchQueue()
        item1 = queue.add_item(tmp_path / "a1", tmp_path / "s1", "First")
        queue.add_item(tmp_path / "a2", tmp_path / "s2", "Second")

        next_item = queue.get_next_pending()
        assert next_item is not None
        assert next_item.id == item1.id

    def test_get_next_pending_skips_non_pending(self, tmp_path):
        queue = BatchQueue()
        item1 = queue.add_item(tmp_path / "a1", tmp_path / "s1", "First")
        item2 = queue.add_item(tmp_path / "a2", tmp_path / "s2", "Second")
        item1.status = QueueItemStatus.COMPLETED

        next_item = queue.get_next_pending()
        assert next_item is not None
        assert next_item.id == item2.id

    def test_get_next_pending_none_when_empty(self):
        queue = BatchQueue()
        assert queue.get_next_pending() is None

    def test_get_next_pending_none_when_all_done(self, tmp_path):
        queue = BatchQueue()
        item = queue.add_item(tmp_path / "a", tmp_path / "s", "Done")
        item.status = QueueItemStatus.COMPLETED
        assert queue.get_next_pending() is None

    def test_get_all_items_returns_copy(self, tmp_path):
        queue = BatchQueue()
        queue.add_item(tmp_path / "a", tmp_path / "s", "Test")
        items = queue.get_all_items()
        items.clear()
        assert queue.total_items == 1  # original not affected

    def test_clear(self, tmp_path):
        queue = BatchQueue()
        queue.add_item(tmp_path / "a1", tmp_path / "s1", "A")
        queue.add_item(tmp_path / "a2", tmp_path / "s2", "B")
        queue.clear()
        assert queue.total_items == 0

    def test_pending_count(self, tmp_path):
        queue = BatchQueue()
        item1 = queue.add_item(tmp_path / "a1", tmp_path / "s1", "A")
        queue.add_item(tmp_path / "a2", tmp_path / "s2", "B")
        queue.add_item(tmp_path / "a3", tmp_path / "s3", "C")
        item1.status = QueueItemStatus.COMPLETED
        assert queue.pending_count == 2

    def test_completed_count(self, tmp_path):
        queue = BatchQueue()
        item1 = queue.add_item(tmp_path / "a1", tmp_path / "s1", "A")
        item2 = queue.add_item(tmp_path / "a2", tmp_path / "s2", "B")
        item1.status = QueueItemStatus.COMPLETED
        item2.status = QueueItemStatus.COMPLETED
        assert queue.completed_count == 2

    def test_total_cards_created(self, tmp_path):
        queue = BatchQueue()
        item1 = queue.add_item(tmp_path / "a1", tmp_path / "s1", "A")
        item2 = queue.add_item(tmp_path / "a2", tmp_path / "s2", "B")
        item1.cards_created = 10
        item2.cards_created = 25
        assert queue.total_cards_created == 35

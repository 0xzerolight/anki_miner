"""Tests for reading_queue module."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.models.reading_queue import (
    ReadingItemStatus,
    ReadingQueue,
    ReadingQueueItem,
)
from anki_miner.services.reading.models import ReadingSourceRef


def _ref(
    kind: str = "mokuro",
    path: str = "/manga/show/vol_01.cbz",
    title: str = "Show",
    volume: str | None = "01",
) -> ReadingSourceRef:
    return ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path(path),
        image_root=None,
        title=title,
        volume=volume,
    )


REF = _ref()

# ---------------------------------------------------------------------------
# ReadingQueueItem defaults
# ---------------------------------------------------------------------------


class TestReadingQueueItemDefaults:
    """ReadingQueueItem construction with only required fields."""

    def test_default_optional_fields(self):
        item = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        assert item.status == ReadingItemStatus.READY
        assert item.cards_created == 0
        assert item.error_message is None

    def test_fields_set(self):
        item = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        assert item.source == REF
        assert item.title == "Show"
        assert item.kind == "mokuro"

    def test_status_can_be_set_at_construction(self):
        item = ReadingQueueItem(
            source=REF,
            title="Show",
            kind="mokuro",
            status=ReadingItemStatus.PROCESSING,
        )
        assert item.status == ReadingItemStatus.PROCESSING


# ---------------------------------------------------------------------------
# ReadingItemStatus
# ---------------------------------------------------------------------------


class TestReadingItemStatus:
    """No probe stage for local files, and no PENDING — the set is smaller."""

    def test_status_members(self):
        assert {s.name for s in ReadingItemStatus} == {
            "READY",
            "PROCESSING",
            "COMPLETED",
            "ERROR",
        }

    def test_status_values(self):
        assert ReadingItemStatus.READY.value == "ready"
        assert ReadingItemStatus.PROCESSING.value == "processing"
        assert ReadingItemStatus.COMPLETED.value == "completed"
        assert ReadingItemStatus.ERROR.value == "error"


# ---------------------------------------------------------------------------
# ReadingQueue.add
# ---------------------------------------------------------------------------


class TestReadingQueueAdd:
    """Tests for ReadingQueue.add."""

    def test_add_returns_ready_item(self):
        queue = ReadingQueue()
        item = queue.add(REF)
        assert item.status == ReadingItemStatus.READY

    def test_add_derives_title_and_kind_from_ref(self):
        queue = ReadingQueue()
        ref = _ref(kind="epub", title="Novel", volume=None)
        item = queue.add(ref)
        assert item.source is ref
        assert item.title == "Novel"
        assert item.kind == "epub"

    def test_add_appends_in_order(self):
        queue = ReadingQueue()
        refs = [
            _ref(title="A", path="/m/a.cbz"),
            _ref(title="B", path="/m/b.cbz"),
            _ref(title="C", path="/m/c.cbz"),
        ]
        for ref in refs:
            queue.add(ref)
        all_items = queue.all_items()
        assert [i.source for i in all_items] == refs

    def test_add_returns_item_in_all_items(self):
        queue = ReadingQueue()
        item = queue.add(REF)
        assert item in queue.all_items()

    def test_add_multiple_returns_distinct_objects(self):
        queue = ReadingQueue()
        item1 = queue.add(REF)
        item2 = queue.add(REF)
        assert item1 is not item2


# ---------------------------------------------------------------------------
# ReadingQueue.all_items
# ---------------------------------------------------------------------------


class TestReadingQueueAllItems:
    """Tests for ReadingQueue.all_items."""

    def test_all_items_returns_copy(self):
        queue = ReadingQueue()
        queue.add(REF)
        snapshot = queue.all_items()
        snapshot.clear()
        assert len(queue.all_items()) == 1  # original not affected

    def test_all_items_empty_on_new_queue(self):
        queue = ReadingQueue()
        assert queue.all_items() == []


# ---------------------------------------------------------------------------
# ReadingQueue.remove
# ---------------------------------------------------------------------------


class TestReadingQueueRemove:
    """Tests for ReadingQueue.remove."""

    def test_remove_drops_item(self):
        queue = ReadingQueue()
        item = queue.add(REF)
        queue.remove(item)
        assert item not in queue.all_items()

    def test_remove_decrements_length(self):
        queue = ReadingQueue()
        item1 = queue.add(REF)
        queue.add(_ref(title="B", path="/m/b.cbz"))
        queue.remove(item1)
        assert len(queue.all_items()) == 1

    def test_remove_leaves_other_items(self):
        queue = ReadingQueue()
        item1 = queue.add(REF)
        item2 = queue.add(_ref(title="B", path="/m/b.cbz"))
        queue.remove(item1)
        assert item2 in queue.all_items()

    def test_remove_non_member_raises_value_error(self):
        queue = ReadingQueue()
        orphan = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        with pytest.raises(ValueError):
            queue.remove(orphan)

    def test_remove_already_removed_raises_value_error(self):
        queue = ReadingQueue()
        item = queue.add(REF)
        queue.remove(item)
        with pytest.raises(ValueError):
            queue.remove(item)

    def test_remove_uses_identity_not_equality(self):
        """remove() must remove the specific instance, not the first field-equal item."""
        queue = ReadingQueue()
        item_a = queue.add(REF)
        item_b = queue.add(REF)
        queue.remove(item_b)
        assert len(queue.all_items()) == 1
        assert queue.all_items()[0] is item_a


# ---------------------------------------------------------------------------
# eq=False identity equality
# ---------------------------------------------------------------------------


class TestReadingQueueItemIdentity:
    """eq=False: two field-equal items are still distinct objects."""

    def test_field_equal_items_are_distinct(self):
        item_a = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        item_b = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        assert item_a != item_b
        assert item_a == item_a

"""Tests for reading_queue module.

The persistent ``ReadingQueue`` collection was removed with the manga tab's
queue; each Preview/Mine run now builds an ephemeral ``ReadingQueueItem`` list
handed straight to the worker. Only the item + status models remain.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.models.reading_queue import (
    ReadingItemStatus,
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
# eq=False identity equality
# ---------------------------------------------------------------------------


class TestReadingQueueItemIdentity:
    """eq=False: two field-equal items are still distinct objects."""

    def test_field_equal_items_are_distinct(self):
        item_a = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        item_b = ReadingQueueItem(source=REF, title="Show", kind="mokuro")
        assert item_a != item_b
        assert item_a == item_a

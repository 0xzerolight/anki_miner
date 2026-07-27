"""Tests for :class:`AudiobookQueueItemWidget`.

Rewritten for D31's calm rows: the glyph, the second line and the per-row
remove button are gone, so these assert the state word, the result count, the
hover detail, the selection hook and the font-metric row height instead.
Mirrors ``test_youtube_queue_item_widget.py``.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui.widgets.audiobook_queue_item_widget import (
    AudiobookQueueItemWidget,
    queue_bucket,
)
from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.models.audiobook_queue import AudiobookQueueItem
from anki_miner.models.mining_queue import ReadyItemStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    audio: str = "/audio/novel_volume_1.m4b",
    sub: str = "/audio/novel_volume_1.srt",
    status: ReadyItemStatus = ReadyItemStatus.READY,
) -> AudiobookQueueItem:
    return AudiobookQueueItem(
        audio_file=Path(audio),
        subtitle_file=Path(sub),
        status=status,
    )


# ---------------------------------------------------------------------------
# READY rendering
# ---------------------------------------------------------------------------


def test_ready_state_word(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.state_label.text() == "Ready"


def test_ready_title_is_audio_filename(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.title_label.full_text == "novel_volume_1.m4b"


def test_ready_subtitle_moves_to_the_hover_detail(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.toolTip() == "novel_volume_1.srt"


def test_ready_has_no_result_yet(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.result_label.text() == ""


# ---------------------------------------------------------------------------
# PROCESSING rendering
# ---------------------------------------------------------------------------


def test_processing_state_word(qtbot) -> None:
    item = _make_item(status=ReadyItemStatus.PROCESSING)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.state_label.text() == "Running"


def test_processing_keeps_subtitle_detail(qtbot) -> None:
    item = _make_item(status=ReadyItemStatus.PROCESSING)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.toolTip() == "novel_volume_1.srt"


# ---------------------------------------------------------------------------
# COMPLETED rendering
# ---------------------------------------------------------------------------


def test_completed_state_word(qtbot) -> None:
    item = _make_item(status=ReadyItemStatus.COMPLETED)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.state_label.text() == "Complete"


def test_completed_shows_card_count(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = ReadyItemStatus.COMPLETED
    item.cards_created = 42
    widget.update_from(item)

    assert widget.result_label.text() == "42 cards"


# ---------------------------------------------------------------------------
# ERROR rendering
# ---------------------------------------------------------------------------


def test_error_state_word(qtbot) -> None:
    item = _make_item(status=ReadyItemStatus.ERROR)
    item.error_message = "boom"
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.state_label.text() == "Failed"


def test_error_message_is_reachable_on_hover(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = ReadyItemStatus.ERROR
    item.error_message = "ffmpeg exploded"
    widget.update_from(item)

    assert "ffmpeg exploded" in widget.toolTip()


def test_error_without_message_shows_empty_detail(qtbot) -> None:
    item = _make_item(status=ReadyItemStatus.ERROR)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.toolTip() == ""


# ---------------------------------------------------------------------------
# update_from idempotency + status round-trip
# ---------------------------------------------------------------------------


def test_update_from_idempotent(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = ReadyItemStatus.COMPLETED
    item.cards_created = 7
    widget.update_from(item)
    first = (widget.state_label.text(), widget.title_label.full_text, widget.result_label.text())
    widget.update_from(item)
    second = (widget.state_label.text(), widget.title_label.full_text, widget.result_label.text())
    assert first == second == ("Complete", "novel_volume_1.m4b", "7 cards")


def test_processing_then_ready_returns_to_the_ready_word(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = ReadyItemStatus.PROCESSING
    widget.update_from(item)
    assert widget.state_label.text() == "Running"

    item.status = ReadyItemStatus.READY
    widget.update_from(item)
    assert widget.state_label.text() == "Ready"
    assert widget.toolTip() == "novel_volume_1.srt"


# ---------------------------------------------------------------------------
# Filter buckets, selection, height
# ---------------------------------------------------------------------------


def test_bucket_per_status() -> None:
    assert queue_bucket(_make_item(status=ReadyItemStatus.READY)) == "ready"
    assert queue_bucket(_make_item(status=ReadyItemStatus.PROCESSING)) == "running"
    assert queue_bucket(_make_item(status=ReadyItemStatus.COMPLETED)) == "complete"
    assert queue_bucket(_make_item(status=ReadyItemStatus.ERROR)) == "failed"


def test_row_carries_the_selection_hook(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)

    widget.set_selected(True)

    assert widget.property("queueSelected") is True


def test_row_height_is_font_metric(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.sizeHint().height() == metric_row_height(widget, vertical_padding=widget.ROW_PADDING_Y)

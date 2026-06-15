"""Tests for :class:`AudiobookQueueItemWidget`.

Exercises the status-to-render mapping, the second (detail) line, the
remove-button enable/disable, idempotency of ``update_from``, and signal
emission in headless Qt mode. Mirrors ``test_youtube_queue_item_widget.py``.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui.widgets.audiobook_queue_item_widget import AudiobookQueueItemWidget
from anki_miner.models.audiobook_queue import AudiobookItemStatus, AudiobookQueueItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    audio: str = "/audio/novel_volume_1.m4b",
    sub: str = "/audio/novel_volume_1.srt",
    status: AudiobookItemStatus = AudiobookItemStatus.READY,
) -> AudiobookQueueItem:
    return AudiobookQueueItem(
        audio_file=Path(audio),
        subtitle_file=Path(sub),
        status=status,
    )


# ---------------------------------------------------------------------------
# READY rendering
# ---------------------------------------------------------------------------


def test_ready_glyph(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.status_label.text() == "●"


def test_ready_title_is_audio_filename(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.title_label.full_text == "novel_volume_1.m4b"


def test_ready_detail_is_subtitle_filename(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.detail_label.full_text == "novel_volume_1.srt"


def test_ready_remove_enabled(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    assert widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# PROCESSING rendering
# ---------------------------------------------------------------------------


def test_processing_glyph(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.PROCESSING)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.status_label.text() == "▶"


def test_processing_remove_disabled(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = AudiobookItemStatus.PROCESSING
    widget.update_from(item)

    assert not widget.remove_button.isEnabled()


def test_processing_keeps_subtitle_detail(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.PROCESSING)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.detail_label.full_text == "novel_volume_1.srt"


# ---------------------------------------------------------------------------
# COMPLETED rendering
# ---------------------------------------------------------------------------


def test_completed_glyph(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.COMPLETED)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.status_label.text() == "✓"


def test_completed_detail_shows_card_count(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = AudiobookItemStatus.COMPLETED
    item.cards_created = 42
    widget.update_from(item)

    assert widget.detail_label.full_text == "42 cards created"


def test_completed_remove_enabled(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.COMPLETED)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# ERROR rendering
# ---------------------------------------------------------------------------


def test_error_glyph(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.ERROR)
    item.error_message = "boom"
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.status_label.text() == "✗"


def test_error_detail_shows_error_message(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = AudiobookItemStatus.ERROR
    item.error_message = "ffmpeg exploded"
    widget.update_from(item)

    assert "ffmpeg exploded" in widget.detail_label.full_text


def test_error_without_message_shows_empty_detail(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.ERROR)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.detail_label.full_text == ""


def test_error_remove_enabled(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.ERROR)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# update_from idempotency + status round-trip
# ---------------------------------------------------------------------------


def test_update_from_idempotent(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = AudiobookItemStatus.COMPLETED
    item.cards_created = 7
    widget.update_from(item)
    first = (
        widget.status_label.text(),
        widget.title_label.full_text,
        widget.detail_label.full_text,
        widget.remove_button.isEnabled(),
    )
    widget.update_from(item)
    second = (
        widget.status_label.text(),
        widget.title_label.full_text,
        widget.detail_label.full_text,
        widget.remove_button.isEnabled(),
    )
    assert first == second == ("✓", "novel_volume_1.m4b", "7 cards created", True)


def test_processing_then_ready_reenables_remove(qtbot) -> None:
    item = _make_item()
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    item.status = AudiobookItemStatus.PROCESSING
    widget.update_from(item)
    assert not widget.remove_button.isEnabled()

    item.status = AudiobookItemStatus.READY
    widget.update_from(item)
    assert widget.remove_button.isEnabled()
    assert widget.detail_label.full_text == "novel_volume_1.srt"


# ---------------------------------------------------------------------------
# removed signal
# ---------------------------------------------------------------------------


def test_removed_signal_fires_on_click(qtbot) -> None:
    widget = AudiobookQueueItemWidget(_make_item())
    qtbot.addWidget(widget)
    fired: list[None] = []
    widget.removed.connect(lambda: fired.append(None))

    widget.remove_button.click()

    assert len(fired) == 1


def test_removed_signal_not_fired_when_disabled(qtbot) -> None:
    item = _make_item(status=AudiobookItemStatus.PROCESSING)
    widget = AudiobookQueueItemWidget(item)
    qtbot.addWidget(widget)

    fired: list[None] = []
    widget.removed.connect(lambda: fired.append(None))

    widget.remove_button.click()  # button is disabled — click is a no-op

    assert len(fired) == 0

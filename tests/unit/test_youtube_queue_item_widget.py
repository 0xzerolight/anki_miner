"""Tests for :class:`YouTubeQueueItemWidget`.

Rewritten for D31's calm rows: the status glyph, the second line and the
per-row remove button are gone, so these assert the state word, the duration
aside, the result count, the hover detail, the selection hook and the
font-metric row height instead.
"""

from __future__ import annotations

from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.gui.widgets.youtube_queue_item_widget import (
    YouTubeQueueItemWidget,
    queue_bucket,
)
from anki_miner.models.youtube import VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_video_info(
    *,
    video_id: str = "vid123",
    title: str = "Test Video",
    duration_s: int = 754,
    has_manual_ja_subs: bool = True,
    has_auto_ja_subs: bool = False,
    is_live: bool = False,
    is_age_restricted: bool = False,
) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=title,
        duration_s=duration_s,
        has_manual_ja_subs=has_manual_ja_subs,
        has_auto_ja_subs=has_auto_ja_subs,
        is_live=is_live,
        is_age_restricted=is_age_restricted,
    )


def _pending_item(url: str = "https://youtu.be/abc") -> YouTubeQueueItem:
    return YouTubeQueueItem(url=url, status=YouTubeItemStatus.PENDING)


# ---------------------------------------------------------------------------
# Duration aside (via widget behaviour — no direct import of _format_duration)
# ---------------------------------------------------------------------------


def test_ready_duration_over_hour(qtbot) -> None:
    """H:MM:SS format kicks in at >= 3600 s."""
    item = _pending_item()
    item.video_info = _make_video_info(duration_s=3725)
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.aside_label.text() == "1:02:05"


# ---------------------------------------------------------------------------
# PENDING / PROBING
# ---------------------------------------------------------------------------


def test_pending_item_title_contains_url(qtbot) -> None:
    url = "https://youtu.be/abc123"
    item = _pending_item(url)
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)

    assert widget.title_label.full_text == url


def test_pending_item_no_duration_text(qtbot) -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    assert widget.aside_label.text() == ""


def test_probing_says_checking(qtbot) -> None:
    """A probe is not a mining run, so it gets its own word."""
    item = YouTubeQueueItem(url="https://www.youtube.com/watch?v=xyz", status=YouTubeItemStatus.PROBING)
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.state_label.text() == "Checking"
    assert widget.title_label.full_text == "https://www.youtube.com/watch?v=xyz"


def test_probing_with_display_title_shows_title(qtbot) -> None:
    """Playlist expansion pre-sets display_title so PROBING rows name the entry."""
    item = YouTubeQueueItem(
        url="https://www.youtube.com/watch?v=xyz",
        status=YouTubeItemStatus.PROBING,
        display_title="Episode 3 — 日本語",
    )
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.title_label.full_text == "Episode 3 — 日本語"
    assert widget.state_label.text() == "Checking"


# ---------------------------------------------------------------------------
# READY
# ---------------------------------------------------------------------------


def test_ready_shows_video_title(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info(title="My Great Video")
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.title_label.full_text == "My Great Video"
    assert widget.state_label.text() == "Ready"


def test_ready_duration_formatted(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info(duration_s=754)
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.aside_label.text() == "12:34"


def test_ready_manual_sub_source_is_hover_detail(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.toolTip() == "Manual JA subs"


def test_ready_auto_sub_source_is_hover_detail(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "auto_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.toolTip() == "Auto JA subs"


def test_ready_auto_dub_sub_source_is_hover_detail(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "auto_dub"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.toolTip() == "Auto JA subs (dub audio)"


def test_sub_mode_label_covers_every_sub_mode() -> None:
    """Every SubMode value must have a label, so a future mode can't ship blank."""
    from typing import get_args

    from anki_miner.gui.widgets.youtube_queue_item_widget import _SUB_MODE_LABEL
    from anki_miner.models.youtube import SubMode

    assert set(_SUB_MODE_LABEL) == set(get_args(SubMode))


# ---------------------------------------------------------------------------
# PROCESSING / COMPLETED
# ---------------------------------------------------------------------------


def test_processing_says_running(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.PROCESSING
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.state_label.text() == "Running"


def test_completed_shows_card_count(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.COMPLETED
    item.resolved_sub_mode = "manual_only"
    item.cards_created = 42

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.state_label.text() == "Complete"
    assert widget.result_label.text() == "42 cards"


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_probe_error_says_failed_and_keeps_the_message(qtbot) -> None:
    item = _pending_item()
    item.status = YouTubeItemStatus.PROBE_ERROR
    item.error_message = "Video unavailable"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.state_label.text() == "Failed"
    assert "Video unavailable" in widget.toolTip()


def test_error_says_failed_and_keeps_the_message(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info(title="Some Video")
    item.status = YouTubeItemStatus.ERROR
    item.error_message = "Network timeout"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.title_label.full_text == "Some Video"
    assert widget.state_label.text() == "Failed"
    assert "Network timeout" in widget.toolTip()


def test_error_without_video_info_falls_back_to_url(qtbot) -> None:
    url = "https://www.youtube.com/watch?v=zzz"
    item = YouTubeQueueItem(
        url=url,
        status=YouTubeItemStatus.ERROR,
        video_info=None,
        error_message="network timeout",
    )
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.title_label.full_text == url
    assert "network timeout" in widget.toolTip()


def test_long_multiline_probe_error_stays_off_the_row(qtbot) -> None:
    """A long multi-line yt-dlp error must not distort the row (Issue #64).

    Under D31 the row shows the video's own title and the word Failed; the
    error is reachable on hover, verbatim.
    """
    long_error = (
        "yt-dlp metadata probe failed (exit 1): WARNING: [youtube] KaRer8-y16M: "
        "n challenge solving failed: Some formats may be missing.\n"
        "WARNING: Only images are available for download, use --list-formats to see them"
    )
    item = _pending_item()
    item.status = YouTubeItemStatus.PROBE_ERROR
    item.error_message = long_error

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.toolTip() == long_error
    assert "\n" not in widget.title_label.text()


# ---------------------------------------------------------------------------
# Filter buckets, selection, height
# ---------------------------------------------------------------------------


def test_bucket_per_status() -> None:
    def bucket(status: YouTubeItemStatus) -> str:
        item = _pending_item()
        item.status = status
        return queue_bucket(item)

    assert bucket(YouTubeItemStatus.PENDING) == "running"
    assert bucket(YouTubeItemStatus.PROBING) == "running"
    assert bucket(YouTubeItemStatus.READY) == "ready"
    assert bucket(YouTubeItemStatus.PROBE_ERROR) == "failed"
    assert bucket(YouTubeItemStatus.PROCESSING) == "running"
    assert bucket(YouTubeItemStatus.COMPLETED) == "complete"
    assert bucket(YouTubeItemStatus.ERROR) == "failed"


def test_row_carries_the_selection_hook(qtbot) -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)

    widget.set_selected(True)

    assert widget.property("queueSelected") is True


def test_row_height_is_font_metric(qtbot) -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    assert widget.sizeHint().height() == metric_row_height(widget, vertical_padding=widget.ROW_PADDING_Y)

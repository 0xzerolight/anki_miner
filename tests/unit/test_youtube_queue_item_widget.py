"""Tests for :class:`YouTubeQueueItemWidget`.

Exercises the status-to-render mapping, duration formatting, sub source line,
remove-button enable/disable, and signal emission in headless Qt mode.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.youtube_queue_item_widget import YouTubeQueueItemWidget
from anki_miner.models.youtube import VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem

# QApplication needed for widget instantiation.
_app = QApplication.instance() or QApplication([])


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
    thumbnail_url: str | None = None,
    uploader: str | None = "Some Channel",
    is_live: bool = False,
    is_age_restricted: bool = False,
) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=title,
        duration_s=duration_s,
        has_manual_ja_subs=has_manual_ja_subs,
        has_auto_ja_subs=has_auto_ja_subs,
        thumbnail_url=thumbnail_url,
        uploader=uploader,
        is_live=is_live,
        is_age_restricted=is_age_restricted,
    )


def _pending_item(url: str = "https://youtu.be/abc") -> YouTubeQueueItem:
    return YouTubeQueueItem(url=url, status=YouTubeItemStatus.PENDING)


# ---------------------------------------------------------------------------
# Duration formatting (via widget behaviour — no direct import of _format_duration)
# ---------------------------------------------------------------------------


def test_ready_duration_over_hour() -> None:
    """H:MM:SS format kicks in at >= 3600 s."""
    item = _pending_item()
    item.video_info = _make_video_info(duration_s=3725)
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert widget.duration_label.text() == "1:02:05"


# ---------------------------------------------------------------------------
# Test 1 — constructor renders a PENDING item
# ---------------------------------------------------------------------------


def test_pending_item_title_contains_url() -> None:
    url = "https://youtu.be/abc123"
    item = _pending_item(url)
    widget = YouTubeQueueItemWidget(item)

    title_text = widget.title_label.text()
    assert title_text == url


def test_pending_item_remove_enabled() -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    assert widget.remove_button.isEnabled()


def test_pending_item_no_duration_text() -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    assert widget.duration_label.text() == ""


# ---------------------------------------------------------------------------
# Test 1b — PROBING state
# ---------------------------------------------------------------------------


def test_probing_shows_placeholder() -> None:
    item = YouTubeQueueItem(url="https://www.youtube.com/watch?v=xyz", status=YouTubeItemStatus.PROBING)
    widget = YouTubeQueueItemWidget(item)
    assert widget.title_label.text() == "(probing...)"
    assert widget.duration_label.text() == ""
    assert widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# Test 2 — update_from(READY)
# ---------------------------------------------------------------------------


def test_ready_shows_video_title() -> None:
    item = _pending_item()
    info = _make_video_info(title="My Great Video")
    item.video_info = info
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert widget.title_label.text() == "My Great Video"


def test_ready_duration_formatted() -> None:
    item = _pending_item()
    item.video_info = _make_video_info(duration_s=754)
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert widget.duration_label.text() == "12:34"


def test_ready_manual_sub_source_line() -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert widget.sub_source_label.text() == "Manual JA subs"


def test_ready_auto_sub_source_line() -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "auto_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert widget.sub_source_label.text() == "Auto JA subs"


# ---------------------------------------------------------------------------
# Test 3 — update_from(PROCESSING) disables remove
# ---------------------------------------------------------------------------


def test_processing_remove_disabled() -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.PROCESSING
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert not widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# Test 4 — update_from(COMPLETED) shows card count
# ---------------------------------------------------------------------------


def test_completed_shows_card_count() -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.COMPLETED
    item.resolved_sub_mode = "manual_only"
    item.cards_created = 42

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    assert "42" in widget.sub_source_label.text()


# ---------------------------------------------------------------------------
# Test 5 — update_from(PROBE_ERROR) shows error message
# ---------------------------------------------------------------------------


def test_probe_error_shows_error_message() -> None:
    item = _pending_item()
    item.status = YouTubeItemStatus.PROBE_ERROR
    item.error_message = "Video unavailable"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    combined = widget.title_label.text() + widget.sub_source_label.text()
    assert "Video unavailable" in combined


# ---------------------------------------------------------------------------
# Test 6 — update_from(ERROR) shows error message
# ---------------------------------------------------------------------------


def test_error_shows_error_message() -> None:
    item = _pending_item()
    item.video_info = _make_video_info(title="Some Video")
    item.status = YouTubeItemStatus.ERROR
    item.error_message = "Network timeout"

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    combined = widget.title_label.text() + widget.sub_source_label.text()
    assert "Network timeout" in combined


def test_error_without_video_info_falls_back_to_url() -> None:
    """ERROR with no video_info shows item URL in the title and error in sub-source."""
    url = "https://www.youtube.com/watch?v=zzz"
    item = YouTubeQueueItem(
        url=url,
        status=YouTubeItemStatus.ERROR,
        video_info=None,
        error_message="network timeout",
    )
    widget = YouTubeQueueItemWidget(item)
    assert widget.title_label.text() == url
    assert "network timeout" in widget.sub_source_label.text()


# ---------------------------------------------------------------------------
# Test 7 — removed signal fires when button is clicked
# ---------------------------------------------------------------------------


def test_removed_signal_fires_on_click() -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    fired: list[None] = []
    widget.removed.connect(lambda: fired.append(None))

    widget.remove_button.click()

    assert len(fired) == 1


# ---------------------------------------------------------------------------
# Test 8 — removed signal does NOT fire when remove is disabled (PROCESSING)
# ---------------------------------------------------------------------------


def test_removed_signal_not_fired_when_disabled() -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.PROCESSING

    widget = YouTubeQueueItemWidget(_pending_item())
    widget.update_from(item)

    fired: list[None] = []
    widget.removed.connect(lambda: fired.append(None))

    widget.remove_button.click()  # button is disabled — click is a no-op

    assert len(fired) == 0

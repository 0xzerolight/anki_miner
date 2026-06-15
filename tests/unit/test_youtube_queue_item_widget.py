"""Tests for :class:`YouTubeQueueItemWidget`.

Exercises the status-to-render mapping, duration formatting, sub source line,
remove-button enable/disable, and signal emission in headless Qt mode.
"""

from __future__ import annotations

from anki_miner.gui.widgets.youtube_queue_item_widget import YouTubeQueueItemWidget
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
# Duration formatting (via widget behaviour — no direct import of _format_duration)
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

    assert widget.duration_label.text() == "1:02:05"


# ---------------------------------------------------------------------------
# Test 1 — constructor renders a PENDING item
# ---------------------------------------------------------------------------


def test_pending_item_title_contains_url(qtbot) -> None:
    url = "https://youtu.be/abc123"
    item = _pending_item(url)
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)

    title_text = widget.title_label.full_text
    assert title_text == url


def test_pending_item_remove_enabled(qtbot) -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    assert widget.remove_button.isEnabled()


def test_pending_item_no_duration_text(qtbot) -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    assert widget.duration_label.text() == ""


# ---------------------------------------------------------------------------
# Test 1b — PROBING state
# ---------------------------------------------------------------------------


def test_probing_shows_placeholder(qtbot) -> None:
    item = YouTubeQueueItem(url="https://www.youtube.com/watch?v=xyz", status=YouTubeItemStatus.PROBING)
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.title_label.full_text == "(probing...)"
    assert widget.duration_label.text() == ""
    assert widget.remove_button.isEnabled()


def test_probing_with_display_title_shows_title(qtbot) -> None:
    """Playlist expansion pre-sets display_title so PROBING rows show the entry title."""
    item = YouTubeQueueItem(
        url="https://www.youtube.com/watch?v=xyz",
        status=YouTubeItemStatus.PROBING,
        display_title="Episode 3 — 日本語",
    )
    widget = YouTubeQueueItemWidget(item)
    qtbot.addWidget(widget)
    assert widget.title_label.full_text == "Episode 3 — 日本語 (probing...)"
    assert widget.duration_label.text() == ""
    assert widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# Test 2 — update_from(READY)
# ---------------------------------------------------------------------------


def test_ready_shows_video_title(qtbot) -> None:
    item = _pending_item()
    info = _make_video_info(title="My Great Video")
    item.video_info = info
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.title_label.full_text == "My Great Video"


def test_ready_duration_formatted(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info(duration_s=754)
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.duration_label.text() == "12:34"


def test_ready_manual_sub_source_line(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.sub_source_label.full_text == "Manual JA subs"


def test_ready_auto_sub_source_line(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.READY
    item.resolved_sub_mode = "auto_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert widget.sub_source_label.full_text == "Auto JA subs"


# ---------------------------------------------------------------------------
# Test 3 — update_from(PROCESSING) disables remove
# ---------------------------------------------------------------------------


def test_processing_remove_disabled(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.PROCESSING
    item.resolved_sub_mode = "manual_only"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert not widget.remove_button.isEnabled()


# ---------------------------------------------------------------------------
# Test 4 — update_from(COMPLETED) shows card count
# ---------------------------------------------------------------------------


def test_completed_shows_card_count(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.COMPLETED
    item.resolved_sub_mode = "manual_only"
    item.cards_created = 42

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    assert "42" in widget.sub_source_label.full_text


# ---------------------------------------------------------------------------
# Test 5 — update_from(PROBE_ERROR) shows error message
# ---------------------------------------------------------------------------


def test_probe_error_shows_error_message(qtbot) -> None:
    item = _pending_item()
    item.status = YouTubeItemStatus.PROBE_ERROR
    item.error_message = "Video unavailable"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    combined = widget.title_label.full_text + widget.sub_source_label.full_text
    assert "Video unavailable" in combined


# ---------------------------------------------------------------------------
# Test 6 — update_from(ERROR) shows error message
# ---------------------------------------------------------------------------


def test_error_shows_error_message(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info(title="Some Video")
    item.status = YouTubeItemStatus.ERROR
    item.error_message = "Network timeout"

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    combined = widget.title_label.full_text + widget.sub_source_label.full_text
    assert "Network timeout" in combined


def test_error_without_video_info_falls_back_to_url(qtbot) -> None:
    """ERROR with no video_info shows item URL in the title and error in sub-source."""
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
    assert "network timeout" in widget.sub_source_label.full_text


# ---------------------------------------------------------------------------
# Test 7 — removed signal fires when button is clicked
# ---------------------------------------------------------------------------


def test_removed_signal_fires_on_click(qtbot) -> None:
    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    fired: list[None] = []
    widget.removed.connect(lambda: fired.append(None))

    widget.remove_button.click()

    assert len(fired) == 1


# ---------------------------------------------------------------------------
# Test 8 — removed signal does NOT fire when remove is disabled (PROCESSING)
# ---------------------------------------------------------------------------


def test_removed_signal_not_fired_when_disabled(qtbot) -> None:
    item = _pending_item()
    item.video_info = _make_video_info()
    item.status = YouTubeItemStatus.PROCESSING

    widget = YouTubeQueueItemWidget(_pending_item())
    qtbot.addWidget(widget)
    widget.update_from(item)

    fired: list[None] = []
    widget.removed.connect(lambda: fired.append(None))

    widget.remove_button.click()  # button is disabled — click is a no-op

    assert len(fired) == 0


# ---------------------------------------------------------------------------
# Test 9 — long multi-line probe errors stay on one line (Issue #64 screenshot)
# ---------------------------------------------------------------------------


def test_long_multiline_probe_error_collapses_to_one_line(qtbot) -> None:
    """A long multi-line yt-dlp probe error must render on a single elided line.

    Regression for the Issue #64 screenshot: the row clipped multi-line error text
    because the title label rendered the embedded ``\\n`` as a second line that the
    fixed-height row then sliced off. The full text must remain reachable via
    ``full_text`` (and hence the hover tooltip).
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

    # Full error preserved verbatim (newline intact) for the tooltip.
    assert long_error in widget.title_label.full_text
    assert widget.title_label.full_text == f"Probe failed: {long_error}"
    # Displayed text is a single line — no embedded newline survives.
    assert "\n" not in widget.title_label.text()

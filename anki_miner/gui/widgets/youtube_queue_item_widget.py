"""Renders a single YouTubeQueueItem as one calm queue-list row (D31).

The row states three facts on one line -- title, state word, result count --
plus the video's duration as a short static aside. Everything that used to
crowd it (the status glyph, the second line, the per-row remove button, and the
live progress text during a run) is gone: removal is a selection action on the
list, and live detail belongs to the one current-job strip above it.

The probe error and the resolved subtitle source stay reachable on hover rather
than on the row, so a 200-item queue reads as a list instead of a wall.
"""

from __future__ import annotations

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from anki_miner.gui.widgets.base.queue_row import QueueRowWidget, state_word
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem
from anki_miner.utils.i18n import tr_format

# Sub-mode label keys (translated at use site via QCoreApplication.translate)
_SUB_MODE_LABEL: dict[str, str] = {
    "manual_only": QT_TRANSLATE_NOOP("YouTubeQueueItemWidget", "Manual JA subs"),
    "auto_only": QT_TRANSLATE_NOOP("YouTubeQueueItemWidget", "Auto JA subs"),
    "auto_dub": QT_TRANSLATE_NOOP("YouTubeQueueItemWidget", "Auto JA subs (dub audio)"),
}

# ---------------------------------------------------------------------------
# Status -> filter bucket. YouTube's probe states have no bucket of their own:
# a probe is work in flight (Running) and a failed probe is a failed row
# (Failed), which is also what "Retry selected" acts on.
# ---------------------------------------------------------------------------
_BUCKETS: dict[YouTubeItemStatus, str] = {
    YouTubeItemStatus.PENDING: "running",
    YouTubeItemStatus.PROBING: "running",
    YouTubeItemStatus.READY: "ready",
    YouTubeItemStatus.PROBE_ERROR: "failed",
    YouTubeItemStatus.PROCESSING: "running",
    YouTubeItemStatus.COMPLETED: "complete",
    YouTubeItemStatus.ERROR: "failed",
}


def queue_bucket(item: YouTubeQueueItem) -> str:
    """Return the filter bucket (``ready``/``running``/``failed``/``complete``)."""
    return _BUCKETS.get(item.status, "running")


def _format_duration(seconds: int) -> str:
    """Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds. Non-positive values return ``""``.

    Returns:
        ``"M:SS"`` for < 3600 s, ``"H:MM:SS"`` for >= 3600 s, ``""`` for <= 0.

    Examples::

        >>> _format_duration(0)
        ''
        >>> _format_duration(59)
        '0:59'
        >>> _format_duration(65)
        '1:05'
        >>> _format_duration(3725)
        '1:02:05'
    """
    if seconds <= 0:
        return ""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


class YouTubeQueueItemWidget(QueueRowWidget):
    """Renders one :class:`~anki_miner.models.youtube_queue.YouTubeQueueItem`.

    A pure renderer -- all business state lives in the item dataclass passed to
    :meth:`update_from`.
    """

    def __init__(self, item: YouTubeQueueItem, parent=None) -> None:
        """Create the widget and render the initial state from *item*.

        Args:
            item: The queue item to render.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setObjectName("yt-queue-item")
        self.update_from(item)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from(self, item: YouTubeQueueItem) -> None:
        """Refresh the visual state from *item*.

        Idempotent -- safe to call repeatedly with the same item object.

        Args:
            item: Current queue item snapshot.
        """
        self.render_row(
            title=self._resolve_title(item),
            aside=self._resolve_duration(item),
            state=self._resolve_state(item),
            result=self._resolve_result(item),
            detail=self._resolve_detail(item),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_title(self, item: YouTubeQueueItem) -> str:
        """Return the row's title: the video title once known, else the URL."""
        if item.video_info is not None:
            return item.video_info.title
        if item.display_title:
            return item.display_title
        return item.url

    def _resolve_duration(self, item: YouTubeQueueItem) -> str:
        """Return the duration aside, once a probe has supplied one."""
        if item.video_info is None:
            return ""
        return _format_duration(item.video_info.duration_s)

    def _resolve_state(self, item: YouTubeQueueItem) -> str:
        """Return the state word. Probing gets its own, since it is not mining."""
        if item.status in (YouTubeItemStatus.PENDING, YouTubeItemStatus.PROBING):
            return self.tr("Checking")
        return state_word(queue_bucket(item))

    def _resolve_result(self, item: YouTubeQueueItem) -> str:
        """Return the result count, which only a completed run has."""
        if item.status == YouTubeItemStatus.COMPLETED:
            return tr_format(self.tr("%1 cards"), item.cards_created)
        return ""

    def _resolve_detail(self, item: YouTubeQueueItem) -> str:
        """Return the hover detail: the failure, else the subtitle source."""
        if item.status in (YouTubeItemStatus.PROBE_ERROR, YouTubeItemStatus.ERROR):
            return item.error_message or ""
        if item.resolved_sub_mode is not None:
            raw = _SUB_MODE_LABEL.get(item.resolved_sub_mode, "")
            return QCoreApplication.translate("YouTubeQueueItemWidget", raw) if raw else ""
        return ""

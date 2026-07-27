"""Renders a single AudiobookQueueItem as one calm queue-list row (D31).

Mirrors ``YouTubeQueueItemWidget`` minus the probe states and the duration
aside (local file pairs need neither): the row states the audio file name, the
state word and the result count on one line, and keeps the subtitle file name
and any failure message on hover.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.base.queue_row import QueueRowWidget, state_word
from anki_miner.models.audiobook_queue import AudiobookQueueItem
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.utils.i18n import tr_format

_BUCKETS: dict[ReadyItemStatus, str] = {
    ReadyItemStatus.READY: "ready",
    ReadyItemStatus.PROCESSING: "running",
    ReadyItemStatus.COMPLETED: "complete",
    ReadyItemStatus.ERROR: "failed",
}


def queue_bucket(item: AudiobookQueueItem) -> str:
    """Return the filter bucket (``ready``/``running``/``failed``/``complete``)."""
    return _BUCKETS.get(item.status, "ready")


class AudiobookQueueItemWidget(QueueRowWidget):
    """Renders one :class:`~anki_miner.models.audiobook_queue.AudiobookQueueItem`.

    A pure renderer -- all business state lives in the item dataclass passed to
    :meth:`update_from`.
    """

    #: File names matter at both ends.
    TITLE_ELIDE_MODE = Qt.TextElideMode.ElideMiddle

    def __init__(self, item: AudiobookQueueItem, parent=None) -> None:
        """Create the widget and render the initial state from *item*.

        Args:
            item: The queue item to render.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setObjectName("audiobook-queue-item")
        self.update_from(item)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from(self, item: AudiobookQueueItem) -> None:
        """Refresh the visual state from *item*.

        Idempotent -- safe to call repeatedly with the same item object.

        Args:
            item: Current queue item snapshot.
        """
        self.render_row(
            title=item.audio_file.name,
            state=state_word(queue_bucket(item)),
            result=self._resolve_result(item),
            detail=self._resolve_detail(item),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_result(self, item: AudiobookQueueItem) -> str:
        """Return the result count, which only a completed run has."""
        if item.status == ReadyItemStatus.COMPLETED:
            return tr_format(self.tr("%1 cards"), item.cards_created)
        return ""

    def _resolve_detail(self, item: AudiobookQueueItem) -> str:
        """Return the hover detail: the failure, else the subtitle file."""
        if item.status == ReadyItemStatus.ERROR:
            return item.error_message or ""
        return item.subtitle_file.name

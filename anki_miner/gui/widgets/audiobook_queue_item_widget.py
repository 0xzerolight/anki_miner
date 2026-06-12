"""Widget that renders a single AudiobookQueueItem as a queue-list row.

Each row shows: status glyph, audio filename, a detail line (subtitle
filename, then cards-created / error after the run), and a remove [×]
button. The remove button is disabled while the item is PROCESSING.
Callers drive all state changes through :meth:`update_from`; the widget
itself holds no business state. Mirrors ``YouTubeQueueItemWidget`` minus
the probe states and duration column (local file pairs need neither).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.models.audiobook_queue import AudiobookItemStatus, AudiobookQueueItem

# ---------------------------------------------------------------------------
# Status → rendering matrix
# Each entry: (glyph, detail_source, remove_enabled)
#
#   READY      ●  subtitle filename       yes
#   PROCESSING ▶  subtitle filename       no
#   COMPLETED  ✓  "N cards created"       yes
#   ERROR      ✗  error_message           yes
# ---------------------------------------------------------------------------
_STATUS_GLYPH: dict[AudiobookItemStatus, str] = {
    AudiobookItemStatus.READY: "●",
    AudiobookItemStatus.PROCESSING: "▶",
    AudiobookItemStatus.COMPLETED: "✓",
    AudiobookItemStatus.ERROR: "✗",
}


class AudiobookQueueItemWidget(QFrame):
    """Renders one :class:`~anki_miner.models.audiobook_queue.AudiobookQueueItem` as a queue-list row.

    The widget is a pure renderer — all business state lives in the item
    dataclass passed to :meth:`update_from`. The only signal it emits is
    :attr:`removed`, which fires when the user clicks the ``[×]`` button.

    Signals:
        removed: Emitted when the user clicks the remove button.
    """

    removed = pyqtSignal()

    def __init__(self, item: AudiobookQueueItem, parent: QWidget | None = None) -> None:
        """Create the widget and render the initial state from *item*.

        Args:
            item: The queue item to render.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._setup_ui()
        self.update_from(item)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from(self, item: AudiobookQueueItem) -> None:
        """Refresh the visual state from *item*.

        Idempotent — safe to call repeatedly with the same item object.

        Args:
            item: Current queue item snapshot.
        """
        status = item.status

        self.status_label.setText(_STATUS_GLYPH.get(status, "●"))
        self.title_label.setText(item.audio_file.name)
        self.detail_label.setText(self._resolve_detail(item))
        self.remove_button.setEnabled(status != AudiobookItemStatus.PROCESSING)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_detail(item: AudiobookQueueItem) -> str:
        """Return the second-line text for the given item state."""
        status = item.status
        if status == AudiobookItemStatus.COMPLETED:
            return f"{item.cards_created} cards created"
        if status == AudiobookItemStatus.ERROR:
            return item.error_message or ""
        return item.subtitle_file.name

    def _setup_ui(self) -> None:
        """Build the widget layout."""
        self.setObjectName("audiobook-queue-item")

        outer = QVBoxLayout()
        outer.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)
        outer.setSpacing(SPACING.xxs)

        # --- top row: glyph | audio filename | [×] ---
        top_row = QHBoxLayout()
        top_row.setSpacing(SPACING.xs)

        # Status glyph
        self.status_label = QLabel()
        self.status_label.setObjectName("audiobook-queue-status-glyph")
        glyph_font = QFont()
        glyph_font.setPixelSize(FONT_SIZES.body)
        self.status_label.setFont(glyph_font)
        self.status_label.setFixedWidth(FONT_SIZES.body + SPACING.xs)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.status_label)

        # Audio filename — elides long names to one line, full text on hover
        # (see ElidingLabel). Keeps the row a constant height across states.
        self.title_label = ElidingLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.title_label.setObjectName("audiobook-queue-title")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.body)
        self.title_label.setFont(title_font)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        top_row.addWidget(self.title_label)

        # Remove button
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("danger")
        self.remove_button.setMaximumWidth(SPACING.xl)
        self.remove_button.setToolTip("Remove from queue")
        self.remove_button.clicked.connect(self.removed.emit)
        top_row.addWidget(self.remove_button)

        outer.addLayout(top_row)

        # --- second row: subtitle filename / cards created / error ---
        self.detail_label = ElidingLabel()
        self.detail_label.setObjectName("audiobook-queue-detail")
        caption_font = QFont()
        caption_font.setPixelSize(FONT_SIZES.caption)
        self.detail_label.setFont(caption_font)
        self.detail_label.setIndent(FONT_SIZES.body + SPACING.xs)  # align under title
        outer.addWidget(self.detail_label)

        self.setLayout(outer)

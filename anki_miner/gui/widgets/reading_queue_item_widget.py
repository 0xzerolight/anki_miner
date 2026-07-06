"""Widget that renders a single ReadingQueueItem as a queue-list row.

Each row shows: status glyph, source title, a detail line (the source kind
— Manga / EPUB / Text — then cards-created / error after the run), and a
remove [×] button. The remove button is disabled while the item is
PROCESSING. Callers drive all state changes through :meth:`update_from`; the
widget itself holds no business state. A faithful clone of
``AudiobookQueueItemWidget`` with the audio/subtitle columns swapped for the
reading title/kind columns.
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
from anki_miner.models.reading_queue import ReadingItemStatus, ReadingQueueItem
from anki_miner.utils.i18n import tr_format

# ---------------------------------------------------------------------------
# Status → rendering matrix
# Each entry: (glyph, detail_source, remove_enabled)
#
#   READY      ●  kind label              yes
#   PROCESSING ▶  kind label              no
#   COMPLETED  ✓  "N cards created"       yes
#   ERROR      ✗  error_message           yes
# ---------------------------------------------------------------------------
_STATUS_GLYPH: dict[ReadingItemStatus, str] = {
    ReadingItemStatus.READY: "●",
    ReadingItemStatus.PROCESSING: "▶",
    ReadingItemStatus.COMPLETED: "✓",
    ReadingItemStatus.ERROR: "✗",
}


class ReadingQueueItemWidget(QFrame):
    """Renders one :class:`~anki_miner.models.reading_queue.ReadingQueueItem` as a queue-list row.

    The widget is a pure renderer — all business state lives in the item
    dataclass passed to :meth:`update_from`. The only signal it emits is
    :attr:`removed`, which fires when the user clicks the ``[×]`` button.

    Signals:
        removed: Emitted when the user clicks the remove button.
    """

    removed = pyqtSignal()

    def __init__(self, item: ReadingQueueItem, parent: QWidget | None = None) -> None:
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

    def update_from(self, item: ReadingQueueItem) -> None:
        """Refresh the visual state from *item*.

        Idempotent — safe to call repeatedly with the same item object.

        Args:
            item: Current queue item snapshot.
        """
        status = item.status

        self.status_label.setText(_STATUS_GLYPH.get(status, "●"))
        self.title_label.setText(item.title)
        self.detail_label.setText(self._resolve_detail(item))
        self.remove_button.setEnabled(status != ReadingItemStatus.PROCESSING)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_detail(self, item: ReadingQueueItem) -> str:
        """Return the second-line text for the given item state."""
        status = item.status
        if status == ReadingItemStatus.COMPLETED:
            return tr_format(self.tr("%1 cards created"), item.cards_created)
        if status == ReadingItemStatus.ERROR:
            return item.error_message or ""
        return self._kind_label(item.kind)

    def _kind_label(self, kind: str) -> str:
        """Human-readable label for the source kind (mokuro/epub/txt)."""
        if kind == "mokuro":
            return self.tr("Manga")
        if kind == "epub":
            return self.tr("EPUB")
        if kind == "txt":
            return self.tr("Text")
        return kind

    def _setup_ui(self) -> None:
        """Build the widget layout."""
        self.setObjectName("reading-queue-item")

        outer = QVBoxLayout()
        outer.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)
        outer.setSpacing(SPACING.xxs)

        # --- top row: glyph | title | [×] ---
        top_row = QHBoxLayout()
        top_row.setSpacing(SPACING.xs)

        # Status glyph
        self.status_label = QLabel()
        self.status_label.setObjectName("reading-queue-status-glyph")
        glyph_font = QFont()
        glyph_font.setPixelSize(FONT_SIZES.body)
        self.status_label.setFont(glyph_font)
        self.status_label.setFixedWidth(FONT_SIZES.body + SPACING.xs)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.status_label)

        # Source title — elides long names to one line, full text on hover
        # (see ElidingLabel). Keeps the row a constant height across states.
        self.title_label = ElidingLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.title_label.setObjectName("reading-queue-title")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.body)
        self.title_label.setFont(title_font)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        top_row.addWidget(self.title_label)

        # Remove button
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("danger")
        self.remove_button.setMaximumWidth(SPACING.xl)
        self.remove_button.setToolTip(self.tr("Remove from queue"))
        self.remove_button.clicked.connect(self.removed.emit)
        top_row.addWidget(self.remove_button)

        outer.addLayout(top_row)

        # --- second row: kind / cards created / error ---
        self.detail_label = ElidingLabel()
        self.detail_label.setObjectName("reading-queue-detail")
        caption_font = QFont()
        caption_font.setPixelSize(FONT_SIZES.caption)
        self.detail_label.setFont(caption_font)
        self.detail_label.setIndent(FONT_SIZES.body + SPACING.xs)  # align under title
        outer.addWidget(self.detail_label)

        self.setLayout(outer)

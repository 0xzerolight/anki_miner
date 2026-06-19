"""Widget that renders a single YouTubeQueueItem as a queue-list row.

Each row shows: status glyph, title (or URL while probing), duration,
sub-source line, and a remove [×] button. The remove button is disabled
while the item is PROCESSING. Callers drive all state changes through
:meth:`update_from`; the widget itself holds no business state.
"""

from __future__ import annotations

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, pyqtSignal
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
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem
from anki_miner.utils.i18n import tr_format

# ---------------------------------------------------------------------------
# Status → rendering matrix
# Each entry: (glyph, title_source, show_duration, remove_enabled)
# title_source is resolved at render time; this table documents the strategy.
#
#   PENDING    ●  item.url                  no   yes
#   PROBING    …  "(probing...)" — or "{display_title} (probing...)" when
#                 playlist expansion pre-set item.display_title   no   yes
#   READY      ●  video_info.title          yes  yes
#   PROBE_ERROR ⚠  error_message            no   yes
#   PROCESSING ▶  video_info.title          yes  no
#   COMPLETED  ✓  video_info.title          yes  yes
#   ERROR      ✗  video_info.title or url   no   yes
# ---------------------------------------------------------------------------
_STATUS_GLYPH: dict[YouTubeItemStatus, str] = {
    YouTubeItemStatus.PENDING: "●",
    YouTubeItemStatus.PROBING: "…",
    YouTubeItemStatus.READY: "●",
    YouTubeItemStatus.PROBE_ERROR: "⚠",
    YouTubeItemStatus.PROCESSING: "▶",
    YouTubeItemStatus.COMPLETED: "✓",
    YouTubeItemStatus.ERROR: "✗",
}

# Sub-mode label keys (translated at use site via QCoreApplication.translate)
_SUB_MODE_LABEL: dict[str, str] = {
    "manual_only": QT_TRANSLATE_NOOP("YouTubeQueueItemWidget", "Manual JA subs"),
    "auto_only": QT_TRANSLATE_NOOP("YouTubeQueueItemWidget", "Auto JA subs"),
}


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


class YouTubeQueueItemWidget(QFrame):
    """Renders one :class:`~anki_miner.models.youtube_queue.YouTubeQueueItem` as a queue-list row.

    The widget is a pure renderer — all business state lives in the item
    dataclass passed to :meth:`update_from`. The only signal it emits is
    :attr:`removed`, which fires when the user clicks the ``[×]`` button.

    Signals:
        removed: Emitted when the user clicks the remove button.
    """

    removed = pyqtSignal()

    def __init__(self, item: YouTubeQueueItem, parent: QWidget | None = None) -> None:
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

    def update_from(self, item: YouTubeQueueItem) -> None:
        """Refresh the visual state from *item*.

        Idempotent — safe to call repeatedly with the same item object.

        Args:
            item: Current queue item snapshot.
        """
        status = item.status

        # --- status glyph ---
        self.status_label.setText(_STATUS_GLYPH.get(status, "●"))

        # --- title ---
        self.title_label.setText(self._resolve_title(item))

        # --- duration ---
        show_duration = status in (
            YouTubeItemStatus.READY,
            YouTubeItemStatus.PROCESSING,
            YouTubeItemStatus.COMPLETED,
        )
        if show_duration and item.video_info is not None:
            self.duration_label.setText(_format_duration(item.video_info.duration_s))
        else:
            self.duration_label.setText("")

        # --- sub source / detail line ---
        self.sub_source_label.setText(self._resolve_sub_source(item))

        # --- remove button ---
        self.remove_button.setEnabled(status != YouTubeItemStatus.PROCESSING)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_title(self, item: YouTubeQueueItem) -> str:
        """Return the appropriate title text for the given item state."""
        status = item.status
        if status == YouTubeItemStatus.PROBING:
            if item.display_title:
                return tr_format(self.tr("%1 (probing...)"), item.display_title)
            return self.tr("(probing...)")
        if status == YouTubeItemStatus.PROBE_ERROR:
            return tr_format(
                self.tr("Probe failed: %1"),
                item.error_message or self.tr("unknown error"),
            )
        if item.video_info is not None:
            return item.video_info.title
        return item.url

    def _resolve_sub_source(self, item: YouTubeQueueItem) -> str:
        """Return the appropriate second-line text for the given item state."""
        status = item.status
        if status == YouTubeItemStatus.COMPLETED:
            return tr_format(self.tr("%1 cards"), item.cards_created)
        if status == YouTubeItemStatus.ERROR:
            return item.error_message or ""
        if item.resolved_sub_mode is not None:
            raw = _SUB_MODE_LABEL.get(item.resolved_sub_mode, "")
            return QCoreApplication.translate("YouTubeQueueItemWidget", raw) if raw else ""
        return ""

    def _setup_ui(self) -> None:
        """Build the widget layout."""
        self.setObjectName("yt-queue-item")

        outer = QVBoxLayout()
        outer.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)
        outer.setSpacing(SPACING.xxs)

        # --- top row: glyph | title | duration | [×] ---
        top_row = QHBoxLayout()
        top_row.setSpacing(SPACING.xs)

        # Status glyph
        self.status_label = QLabel()
        self.status_label.setObjectName("yt-queue-status-glyph")
        glyph_font = QFont()
        glyph_font.setPixelSize(FONT_SIZES.body)
        self.status_label.setFont(glyph_font)
        self.status_label.setFixedWidth(FONT_SIZES.body + SPACING.xs)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.status_label)

        # Title — elides long titles / multi-line probe errors to one line, full text
        # on hover (see ElidingLabel). Keeps the row a constant height across states.
        self.title_label = ElidingLabel()
        self.title_label.setObjectName("yt-queue-title")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.body)
        self.title_label.setFont(title_font)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        top_row.addWidget(self.title_label)

        # Duration
        self.duration_label = QLabel()
        self.duration_label.setObjectName("yt-queue-duration")
        dur_font = QFont()
        dur_font.setPixelSize(FONT_SIZES.caption)
        self.duration_label.setFont(dur_font)
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self.duration_label)

        # Remove button
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("danger")
        self.remove_button.setMaximumWidth(SPACING.xl)
        self.remove_button.setToolTip(self.tr("Remove from queue"))
        self.remove_button.clicked.connect(self.removed.emit)
        top_row.addWidget(self.remove_button)

        outer.addLayout(top_row)

        # --- second row: sub source / detail line ---
        self.sub_source_label = ElidingLabel()
        self.sub_source_label.setObjectName("yt-queue-sub-source")
        caption_font = QFont()
        caption_font.setPixelSize(FONT_SIZES.caption)
        self.sub_source_label.setFont(caption_font)
        self.sub_source_label.setIndent(FONT_SIZES.body + SPACING.xs)  # align under title
        outer.addWidget(self.sub_source_label)

        self.setLayout(outer)

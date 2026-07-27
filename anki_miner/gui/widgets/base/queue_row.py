"""One calm queue row: title, state word, result count (decision D31).

The queue rows used to carry two lines, a status glyph and a per-row remove
button, and during a run the row for the item being mined grew live progress
text. The workers mine strictly one item at a time, so that live text was
duplicated data on every row but one -- and it made a 200-item queue impossible
to read. D31 moves all live detail to a single strip above the list and leaves
each row stating three facts on one line.

Selection is painted here rather than left to the view. Rows are embedded with
``QListWidget.setItemWidget``, so the item's own selection paint sits *behind*
an opaque row and the row's child labels keep the unselected text colour on top
of it. The row therefore paints its own treatment: a full-strength leading bar
plus a translucent wash, both taken from ``QPalette.ColorRole.Highlight``.
That role is written from each theme's ``table-selected-bg`` in
``Theme.apply_to_app``, so one rule follows all 29 themes and this module adds
no colour of its own. ``queueSelected`` is published as a dynamic property so a
stylesheet can add to the treatment without this widget growing a palette.
"""

from __future__ import annotations

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPalette
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.base.sizing import metric_row_height

#: tr-context for the shared state vocabulary. Spelled explicitly (rather than
#: via ``self.tr``) because the concrete rows subclass this widget: ``self.tr``
#: would resolve to the subclass at runtime and to this class at extraction
#: time, and the translated payload would be orphaned.
_TR_CONTEXT = "QueueRow"

#: Filter bucket -> state word. The five filter chips and the row's own word are
#: the same vocabulary on purpose: a row reading "Failed" is exactly what the
#: Failed chip selects.
_STATE_WORDS: dict[str, str] = {
    "ready": QT_TRANSLATE_NOOP("QueueRow", "Ready"),
    "running": QT_TRANSLATE_NOOP("QueueRow", "Running"),
    "failed": QT_TRANSLATE_NOOP("QueueRow", "Failed"),
    "complete": QT_TRANSLATE_NOOP("QueueRow", "Complete"),
}

#: Width of the leading accent bar on a selected row.
_SELECTION_BAR_W = 3
#: Opacity of the highlight wash across the rest of a selected row. Low enough
#: that the row's ordinary text colour stays readable over it in both light and
#: dark themes, which a full-strength fill would not guarantee.
_SELECTION_WASH_ALPHA = 64


def state_word(bucket: str) -> str:
    """Return the translated state word for a filter *bucket*.

    Args:
        bucket: One of ``ready``, ``running``, ``failed``, ``complete``.

    Returns:
        The translated word, or ``""`` for an unknown bucket.
    """
    source = _STATE_WORDS.get(bucket)
    return QCoreApplication.translate(_TR_CONTEXT, source) if source else ""


class QueueRowWidget(QFrame):
    """A single queue row: title, optional aside, state word, result count.

    Subclasses render a concrete queue item by computing those strings and
    calling :meth:`render_row`; they hold no state of their own beyond what the
    item passed in carries.
    """

    #: Breathing room above and below the row's text, per edge.
    ROW_PADDING_Y = SPACING.xs

    #: How the title is truncated. Paths and file names elide in the middle so
    #: both the volume number and the extension survive; prose elides right.
    TITLE_ELIDE_MODE = Qt.TextElideMode.ElideRight

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the one-line layout.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._selected = False
        self.setProperty("queueSelected", False)
        # The list paints the alternating/hover background; a row that filled
        # its own would hide it and defeat the view's own feedback.
        self.setAutoFillBackground(False)
        self._setup_ui()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_row(
        self,
        *,
        title: str,
        state: str,
        result: str,
        aside: str = "",
        detail: str = "",
    ) -> None:
        """Paint one row's worth of facts.

        Args:
            title: What the row is -- a video title, an audio file name.
            state: The state word, from :func:`state_word`.
            result: The run's result for this row, e.g. ``"42 cards"``.
            aside: Short static metadata shown before the state, e.g. a duration.
            detail: Everything that does not belong on a calm row -- the failure
                message, the subtitle source. Reachable on hover.
        """
        self.title_label.setText(title)
        self.aside_label.setText(aside)
        self.state_label.setText(state)
        self.result_label.setText(result)
        self.setToolTip(detail)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def is_selected(self) -> bool:
        """Whether this row is currently part of the selection."""
        return self._selected

    def set_selected(self, selected: bool) -> None:
        """Mirror the view's selection state onto this row.

        Args:
            selected: True when the list has this row selected.
        """
        if selected == self._selected:
            return
        self._selected = selected
        self.setProperty("queueSelected", selected)
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        """Draw the frame, then the selection treatment under the child labels.

        Painting after ``super()`` keeps the treatment above whatever the
        stylesheet drew for the frame; Qt still paints child widgets afterwards,
        so the labels stay on top and legible.
        """
        super().paintEvent(event)
        if not self._selected:
            return

        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        wash = QColor(highlight)
        wash.setAlpha(_SELECTION_WASH_ALPHA)

        painter = QPainter(self)
        painter.fillRect(self.rect(), wash)
        painter.fillRect(QRect(0, 0, _SELECTION_BAR_W, self.height()), highlight)
        painter.end()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Height from the rendered font, so the row tracks the UI text scale."""
        hint = super().sizeHint()
        return QSize(hint.width(), metric_row_height(self, vertical_padding=self.ROW_PADDING_Y))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Lay the four labels out on one line."""
        row = QHBoxLayout()
        row.setContentsMargins(SPACING.sm, 0, SPACING.sm, 0)
        row.setSpacing(SPACING.sm)

        self.title_label = ElidingLabel(mode=self.TITLE_ELIDE_MODE)
        self.title_label.setObjectName("queue-row-title")
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        row.addWidget(self.title_label, 1)

        self.aside_label = QLabel()
        self.aside_label.setObjectName("queue-row-aside")
        self.aside_label.setFont(self._caption_font())
        self.aside_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.aside_label)

        self.state_label = QLabel()
        self.state_label.setObjectName("queue-row-state")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.state_label)

        self.result_label = QLabel()
        self.result_label.setObjectName("queue-row-result")
        self.result_label.setFont(self._caption_font())
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.result_label)

        self.setLayout(row)

    @staticmethod
    def _caption_font():
        """Caption-sized font for the secondary columns."""
        from PyQt6.QtGui import QFont

        font = QFont()
        font.setPixelSize(FONT_SIZES.caption)
        return font

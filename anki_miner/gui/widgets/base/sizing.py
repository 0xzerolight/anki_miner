"""Auto-sizing utilities for Qt widgets.

Provides Unity-style auto-sizing helpers that make widgets adapt to their content
rather than using fixed dimensions.

``apply_button_size``, ``metric_row_height`` and ``page_width_cap`` are the shared
replacement for hard-coded pixel floors on controls, item-view rows and page
columns. Derive geometry from live font metrics through them rather than writing
another constant: a literal floor silently stops tracking the UI text scale, which
is exactly how the 2026-07-25 audit's row-crush and clipped-button findings were
produced.
"""

from enum import Enum

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING

#: Vertical breathing room above and below a button's text, per edge. It is the
#: same step ``common.qss`` pads a button with, so this floor sits just under the
#: height the stylesheet already produces: a floor is insurance against a control
#: that clears no padding at all, not a second opinion about how tall a button is.
_BUTTON_PADDING_Y = SPACING.xxs
#: Default breathing room above and below an item-view row's text, per edge.
_ROW_PADDING_Y = SPACING.xxs

#: A comfortable prose measure, in characters. The classic typographic range is
#: 45-90; 60 sits in the middle of it and is the unit both page widths are built
#: from, so widening a page means "one more column of reading", not "some more
#: pixels".
_READABLE_MEASURE_CH = 60

#: Object name every page shell carries, so QSS and tests have one stable handle
#: on "the scrolled body of a screen".
PAGE_SCROLL_OBJECT_NAME = "page-scroll"


def field_label_width(*texts: str) -> int:
    """Compute a shared column width for a group of ``field-label`` labels.

    Returns the widest preferred width across ``texts`` when each is rendered
    as a ``#field-label`` QLabel, so callers can give every labeled row in a
    section the same label-column width and keep their input fields aligned.

    Each candidate label is polished with ``ensurePolished()`` so the QSS
    padding on ``#field-label`` is included in ``sizeHint()`` regardless of
    whether the real widgets have been added to the widget tree yet.

    Args:
        *texts: The label texts that will share the column.

    Returns:
        The maximum required width in pixels, or 0 when no texts are given.
    """
    width = 0
    for text in texts:
        label = QLabel(text)
        label.setObjectName("field-label")
        label.ensurePolished()
        width = max(width, label.sizeHint().width())
    return width


def make_label_fit_text(label: QLabel) -> None:
    """Make a label only as wide as its text content.

    By default, QLabel expands horizontally to fill available space.
    This sets the horizontal policy to Maximum, so the label (and its
    background) will only be as wide as the text requires.

    Args:
        label: The QLabel to modify
    """
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)


def make_widget_expand_vertically(widget: QWidget) -> None:
    """Allow a widget to grow vertically with its content.

    Sets the vertical size policy to Minimum, which means the widget
    will shrink to fit its content but can expand if content grows.
    This replaces fixed setMinimumHeight() calls.

    Args:
        widget: The widget to modify
    """
    policy = widget.sizePolicy()
    policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
    widget.setSizePolicy(policy)


def make_widget_shrink_to_fit(widget: QWidget) -> None:
    """Make a widget shrink to fit its content in both dimensions.

    Sets both horizontal and vertical policies to Maximum, so the widget
    will only take the space its content requires.

    Args:
        widget: The widget to modify
    """
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)


def apply_button_size(button: QPushButton, *, square: bool = False) -> None:
    """Give ``button`` a font-metric minimum height instead of a pixel floor.

    Replaces per-call-site ``setMinimumHeight(...)`` constants so a control keeps
    clearing its own text at every UI text scale. Deliberately touches geometry
    only -- the caller's size policy is left alone, because stretch is a layout
    decision the call site owns.

    Args:
        button: The button to size.
        square: When True, also pin the width to the height, for glyph-only
            controls such as the chain-editor reorder arrows.
    """
    button.ensurePolished()
    height = button.fontMetrics().height() + 2 * _BUTTON_PADDING_Y
    button.setMinimumHeight(height)

    if square:
        button.setMinimumWidth(height)
        button.setMaximumWidth(height)


def metric_row_height(widget: QWidget, *, vertical_padding: int = _ROW_PADDING_Y) -> int:
    """Return a row height for ``widget`` derived from its rendered font.

    Rows sized from a constant crush their content once the text scale rises
    (Issue #102's class). Sizing from ``fontMetrics`` keeps a row tall enough for
    its own glyphs -- including CJK, which is taller than Latin at the same point
    size.

    Takes any ``QWidget``, not just an item view: queue rows are embedded
    ``QFrame`` widgets set via ``QListWidget.setItemWidget``, and they must derive
    their height from the same rule, or the app ends up with two row metrics that
    disagree at large text scales.

    Uses ``lineSpacing`` rather than ``height`` because it includes the font's
    leading, which is the correct inter-row measure for a row of text.

    Args:
        widget: The view or row widget whose font decides the height.
        vertical_padding: Breathing room applied to each edge.

    Returns:
        The row height in pixels.
    """
    widget.ensurePolished()
    return widget.fontMetrics().lineSpacing() + 2 * vertical_padding


def configure_card_layout(layout: QLayout) -> None:
    """Give a ``#card`` frame's layout the shared padding and gap (D40).

    Fourteen screens hand-built the same card and each set its own margins, so
    "the padding inside a card" was a decision taken thirty-odd times. It is one
    decision: a card already sits inside a page with margins of its own, so its
    inset only has to hold the content off the border.

    Args:
        layout: The card's top-level layout.
    """
    layout.setContentsMargins(SPACING.sm, SPACING.sm, SPACING.sm, SPACING.sm)
    layout.setSpacing(SPACING.xs)


class PageWidth(Enum):
    """How much horizontal measure a screen's content can usefully spend.

    Values are counts of characters, not pixels -- see :func:`page_width_cap`.
    A monitor wider than this buys gutters.

    There is deliberately one member. This started as two -- a narrow FORM
    class and a wide DATA class -- which meant the content column jumped by
    550px as the user moved between sibling tabs, and Deck Builder, capped by
    neither, ran the full window as a third width. Reading that as three
    unrelated screens is exactly what the split produced.

    Keeping form inputs readable is a separate job, done a level down by
    :func:`form_row_cap`, not by narrowing the page they sit on.
    """

    #: One column for every screen: three readable measures.
    PAGE = 3 * _READABLE_MEASURE_CH


def page_width_cap(widget: QWidget, kind: PageWidth) -> int:
    """Return ``kind``'s cap in pixels, measured through ``widget``'s font.

    Uses the advance of ``"0"`` -- the CSS ``ch`` unit, and the one digit every
    font renders at its tabular width -- so the cap holds the same number of
    characters at 0.8x text as it does at 1.5x. A pixel constant would instead
    hand a large-text user the same column with a third of the words in it.

    Args:
        widget: The widget whose rendered font decides the measure.
        kind: The page's declared width class.

    Returns:
        The maximum content width in pixels.
    """
    widget.ensurePolished()
    return kind.value * widget.fontMetrics().horizontalAdvance("0")


def form_row_cap(widget: QWidget) -> int:
    """Return the width one labelled row may spend, measured through ``widget``.

    A page is one column wide for every screen (:class:`PageWidth`), but a form
    row is a label beside its control, and a control given the whole column is
    just a longer empty box -- a 1500px "Video file" field holds no more of a
    path than a 900px one does. So the row stops here and leaves the rest of
    the column as whitespace.

    Two readable measures, which is what the narrow page class used to be, so
    rows come out the width they already were before the column widened.

    Callers must add the row with an explicit ``AlignLeft``: Qt centres a
    layout item whose widget is narrower than its cell unless an alignment flag
    says otherwise (``QWidgetItem::setGeometry`` falls through to the centring
    branch when the horizontal alignment resolves to 0), and a centred form
    column stops lining up with the full-width cards above and below it.

    Args:
        widget: The widget whose rendered font decides the measure.

    Returns:
        The maximum row width in pixels.
    """
    widget.ensurePolished()
    return 2 * _READABLE_MEASURE_CH * widget.fontMetrics().horizontalAdvance("0")


class _RowCapKeeper(QObject):
    """Re-applies a row-field cap whenever the field's font changes.

    The UI text scale is applied live from Settings without rebuilding the
    tabs, so a cap computed once at construction is stale the moment the user
    moves that slider -- and a stale cap on a *grown* font clips the field
    instead of merely narrowing it. Panels that own their rows
    (:class:`~anki_miner.gui.widgets.base.form_panel.FormPanel`,
    :class:`~anki_miner.gui.widgets.enhanced.file_selector.FileSelector`)
    recompute in their own ``changeEvent``; this is the same rule for a row
    some screen built by hand, with no subclass to hang it on.
    """

    def __init__(self, field: QWidget, label_width: int, spacing: int) -> None:
        super().__init__(field)
        self._field = field
        self._label_width = label_width
        self._spacing = spacing
        field.installEventFilter(self)

    def apply(self) -> None:
        cap = form_row_cap(self._field) - self._label_width - self._spacing
        self._field.setMaximumWidth(max(cap, self._field.minimumSizeHint().width()))

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        # Show, not construction: reading ``minimumSizeHint`` activates the
        # layout, and doing that on a widget that has never been shown clears
        # the hidden flag Qt put on its children.
        if event is not None and event.type() in (QEvent.Type.Show, QEvent.Type.FontChange):
            self.apply()
        return False


def cap_row_field(field: QWidget, label_width: int, spacing: int = 0) -> None:
    """Cap a hand-built labelled row's field to the shared row measure (D5).

    For rows a screen assembles itself out of a ``QLabel`` and a control,
    rather than through ``FormPanel`` or ``FileSelector``. Add a trailing
    stretch to the row as well: the cap decides how wide the field is, the
    stretch decides that the slack goes to its right rather than around it.

    Args:
        field: The control beside the label.
        label_width: Width of the row's label column.
        spacing: The row layout's gap between label and field.
    """
    _RowCapKeeper(field, label_width, spacing)


def configure_scrolled_page(scroll: QScrollArea, content: QWidget, kind: PageWidth) -> None:
    """Turn ``scroll``/``content`` into a centred page column capped at ``kind``.

    This is the single page shell: it applies the frame, scroll-bar policy and
    resize behaviour every screen was repeating by hand, then centres the
    content and caps it. Content width ends up at ``min(viewport, cap)``, so a
    1024px window is unaffected and a 3440px one grows gutters instead of
    inputs.

    The cap is never allowed below the content's own minimum. Qt applies a
    widget's maximum *after* its minimum when a resizable scroll area lays it
    out, so a smaller cap would not shrink the page -- it would clip it behind
    the disabled horizontal scrollbar. That corner is reachable in practice at
    0.8x text, where the FORM cap drops under the widest Settings panel.

    Args:
        scroll: The page's scroll area. Its widget is set here; do not also call
            ``setWidget``.
        content: The column of cards the page builds, fully populated.
        kind: The page's declared ``PAGE_WIDTH``.
    """
    scroll.setObjectName(PAGE_SCROLL_OBJECT_NAME)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

    content.ensurePolished()
    needed = max(content.minimumWidth(), content.minimumSizeHint().width())
    content.setMaximumWidth(max(page_width_cap(content, kind), needed))

    scroll.setWidget(content)


def configure_expanding_container(widget: QWidget) -> None:
    """Configure a container widget to expand and accommodate children.

    Sets horizontal policy to Expanding (fills available width) and
    vertical policy to Minimum (shrinks to content but can grow).
    This is ideal for card-style containers.

    Args:
        widget: The container widget to modify
    """
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

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

from PyQt6.QtCore import Qt
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
    A monitor wider than this buys gutters, because a wider "Video file" box
    carries no more information than a narrow one.
    """

    #: A label beside its control: two measures side by side.
    FORM = 2 * _READABLE_MEASURE_CH
    #: Queues, tables and analytics, whose columns really do use the room.
    DATA = 3 * _READABLE_MEASURE_CH


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

"""Auto-sizing utilities for Qt widgets.

Provides Unity-style auto-sizing helpers that make widgets adapt to their content
rather than using fixed dimensions.

``apply_button_size`` and ``metric_row_height`` are the shared replacement for
hard-coded pixel floors on controls and item-view rows. Derive geometry from live
font metrics through them rather than writing another constant: a literal floor
silently stops tracking the UI text scale, which is exactly how the 2026-07-25
audit's row-crush and clipped-button findings were produced.
"""

from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from anki_miner.gui.resources.styles import SPACING

#: Vertical breathing room above and below a button's text, per edge.
_BUTTON_PADDING_Y = SPACING.xs
#: Default breathing room above and below an item-view row's text, per edge.
_ROW_PADDING_Y = SPACING.xxs


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


def configure_expanding_container(widget: QWidget) -> None:
    """Configure a container widget to expand and accommodate children.

    Sets horizontal policy to Expanding (fills available width) and
    vertical policy to Minimum (shrinks to content but can grow).
    This is ideal for card-style containers.

    Args:
        widget: The container widget to modify
    """
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

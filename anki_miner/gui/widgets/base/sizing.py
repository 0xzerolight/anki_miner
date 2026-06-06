"""Auto-sizing utilities for Qt widgets.

Provides Unity-style auto-sizing helpers that make widgets adapt to their content
rather than using fixed dimensions.
"""

from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget


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


def configure_expanding_container(widget: QWidget) -> None:
    """Configure a container widget to expand and accommodate children.

    Sets horizontal policy to Expanding (fills available width) and
    vertical policy to Minimum (shrinks to content but can grow).
    This is ideal for card-style containers.

    Args:
        widget: The container widget to modify
    """
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

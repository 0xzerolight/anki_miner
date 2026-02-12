"""Utility functions for widget styling."""

from PyQt6.QtWidgets import QWidget


def refresh_widget_style(widget: QWidget) -> None:
    """Force a widget to refresh its style after a property change.

    This is necessary when using QSS property selectors like [status="error"].
    After setting a property with setProperty(), call this to apply the new style.

    Args:
        widget: The widget to refresh
    """
    if style := widget.style():
        style.unpolish(widget)
        style.polish(widget)


def format_icon_text(icon: str, text: str) -> str:
    """Combine an icon character with text.

    Args:
        icon: Icon character/emoji (can be empty string)
        text: The text to display

    Returns:
        Combined string, or just text if icon is empty
    """
    if icon:
        return f"{icon} {text}"
    return text

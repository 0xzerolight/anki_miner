"""Design variables for consistent UI styling.

This module provides centralized design tokens as frozen dataclasses for:
- Spacing values
- Font sizes
- Border radius values

Usage in Python:
    from anki_miner.gui.resources.styles._variables import SPACING, FONT_SIZES, BORDER_RADIUS

    layout.setSpacing(SPACING.md)
    font.setPixelSize(FONT_SIZES.h3)

Usage in QSS (after substitution):
    font-size: ${font-size-h3}px;
    padding: ${spacing-md}px;
    border-radius: ${border-radius-large}px;
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Spacing:
    """Spacing values based on 4px/8px grid system."""

    xxs: int = 4
    xs: int = 8
    sm: int = 12
    md: int = 16
    lg: int = 24
    xl: int = 32
    xxl: int = 48


@dataclass(frozen=True)
class FontSizes:
    """Font size values in pixels."""

    h1: int = 24  # Main window title
    h2: int = 20  # Tab titles, dialog headers
    h3: int = 16  # Group boxes, section headers
    body: int = 14  # Default body text
    body_sm: int = 13  # Slightly smaller body text
    caption: int = 12  # Helper text, captions
    small: int = 11  # Status bar, badges
    stat_value: int = 32  # Large stat card numbers
    icon_large: int = 48  # Large dialog icons


@dataclass(frozen=True)
class BorderRadius:
    """Border radius values in pixels."""

    small: int = 4
    default: int = 6
    large: int = 8
    pill: int = 9999  # Fully rounded (pill shape)


# Singleton instances for use throughout the application
SPACING = Spacing()
FONT_SIZES = FontSizes()
BORDER_RADIUS = BorderRadius()


def get_variable_dict() -> dict[str, str]:
    """Get all design variables as a dictionary for QSS substitution.

    Variable names follow the pattern: category-name (e.g., spacing-md, font-size-h1)

    Returns:
        Dictionary mapping variable names to their pixel values (as strings)
    """
    variables = {}

    # Spacing variables
    for field in ["xxs", "xs", "sm", "md", "lg", "xl", "xxl"]:
        value = getattr(SPACING, field)
        variables[f"spacing-{field}"] = str(value)

    # Font size variables
    font_fields = [
        "h1",
        "h2",
        "h3",
        "body",
        "body-sm",
        "caption",
        "small",
        "stat-value",
        "icon-large",
    ]
    font_attrs = [
        "h1",
        "h2",
        "h3",
        "body",
        "body_sm",
        "caption",
        "small",
        "stat_value",
        "icon_large",
    ]
    for field, attr in zip(font_fields, font_attrs, strict=True):
        value = getattr(FONT_SIZES, attr)
        variables[f"font-size-{field}"] = str(value)

    # Border radius variables
    for field in ["small", "default", "large", "pill"]:
        value = getattr(BORDER_RADIUS, field)
        variables[f"border-radius-{field}"] = str(value)

    return variables

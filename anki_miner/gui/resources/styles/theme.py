"""Theme management system for Anki Miner GUI.

This module provides centralized theme management with support for three themes:
- Light: Modern minimalist design with clean typography
- Dark: Comfortable dark palette for night usage
- Sakura: Culturally-inspired aesthetic with cherry blossom motifs
"""

import json
import logging
import re
from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.resources import get_resource_dir

from ._variables import get_variable_dict

logger = logging.getLogger(__name__)

REQUIRED_COLOR_KEYS = frozenset(
    [
        "primary",
        "primary-hover",
        "primary-pressed",
        "primary-light",
        "primary-dark",
        "secondary",
        "background",
        "surface",
        "surface-hover",
        "surface-alt",
        "text",
        "text-muted",
        "text-disabled",
        "text-on-primary",
        "border",
        "border-focus",
        "border-subtle",
        "disabled",
        "input-bg",
        "input-disabled-bg",
        "error",
        "error-hover",
        "success",
        "warning",
        "info",
        "scrollbar",
        "scrollbar-hover",
        "tooltip-bg",
        "tooltip-text",
        "tooltip-border",
        "divider",
        "update-banner-bg",
        "update-banner-text",
        "decorative",
        "badge-success-bg",
        "badge-success-text",
        "badge-warning-bg",
        "badge-warning-text",
        "badge-error-bg",
        "badge-error-text",
        "badge-info-bg",
        "badge-info-text",
        "badge-pending-bg",
        "badge-pending-text",
        "table-selected-bg",
        "table-selected-text",
    ]
)


def validate_theme_data(data: dict) -> list[str]:
    """Validate a theme data dict against the required schema.

    Args:
        data: Parsed JSON theme data

    Returns:
        List of validation error strings. Empty list = valid.
    """
    errors = []

    if "name" not in data:
        errors.append("Missing required field: 'name'")
    elif not isinstance(data["name"], str):
        errors.append("Field 'name' must be a string")

    if "colors" not in data:
        errors.append("Missing required field: 'colors'")
    elif not isinstance(data["colors"], dict):
        errors.append("Field 'colors' must be a dict")
    else:
        missing = REQUIRED_COLOR_KEYS - set(data["colors"].keys())
        if missing:
            errors.append(f"Missing color keys: {', '.join(sorted(missing))}")

    return errors


def discover_themes(themes_dir: Path) -> dict[str, dict]:
    """Discover and load valid theme JSON files from a directory.

    Scans for *.json files, validates each, skips invalid ones with a warning.

    Args:
        themes_dir: Path to the themes directory

    Returns:
        OrderedDict of theme_key -> theme_data, sorted alphabetically by filename.
    """
    themes: dict[str, dict] = {}

    if not themes_dir.is_dir():
        logger.warning("Themes directory not found: %s", themes_dir)
        return themes

    for path in sorted(themes_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping invalid theme file %s: %s", path.name, e)
            continue

        errors = validate_theme_data(data)
        if errors:
            logger.warning("Skipping theme %s: %s", path.name, "; ".join(errors))
            continue

        themes[path.stem] = data

    return themes


def get_color_variables(theme_data: dict) -> dict[str, str]:
    """Extract color variables from theme data for QSS substitution.

    Prefixes each color key with 'color-' to form the variable name
    (e.g., 'primary' -> 'color-primary' for use as ${color-primary} in QSS).

    Args:
        theme_data: Validated theme data dict

    Returns:
        Dict mapping 'color-<key>' -> color value string
    """
    return {f"color-{key}": value for key, value in theme_data["colors"].items()}


ThemeMode = Literal["light", "dark", "sakura"]


class Theme:
    """Centralized theme management for the application.

    This class provides color palettes, spacing constants, typography settings,
    and stylesheet management for all three supported themes.
    """

    # Singleton instance
    _instance = None
    _current_mode: ThemeMode = "light"

    # ============== THEME COLOR PALETTES ==============

    LIGHT_COLORS = {
        # Primary Colors
        "primary": "#6366F1",  # Indigo 500
        "primary_hover": "#4F46E5",  # Indigo 600
        "secondary": "#8B5CF6",  # Purple 500
        # Status Colors
        "success": "#10B981",  # Emerald 500
        "warning": "#F59E0B",  # Amber 500
        "error": "#EF4444",  # Red 500
        "info": "#3B82F6",  # Blue 500
        # Background Colors
        "background": "#F9FAFB",  # Gray 50
        "surface": "#FFFFFF",  # White
        "hover_surface": "#F3F4F6",  # Gray 100
        # Border Colors
        "border": "#E5E7EB",  # Gray 200
        "border_focus": "#6366F1",  # Indigo 500
        # Text Colors
        "text_primary": "#111827",  # Gray 900
        "text_secondary": "#6B7280",  # Gray 500
        "text_disabled": "#9CA3AF",  # Gray 400
        "text_on_primary": "#FFFFFF",  # White
        # Special
        "disabled": "#9CA3AF",  # Gray 400
    }

    DARK_COLORS = {
        # Primary Colors
        "primary": "#6366F1",  # Indigo 500
        "primary_hover": "#818CF8",  # Indigo 400
        "secondary": "#8B5CF6",  # Purple 500
        # Status Colors
        "success": "#10B981",  # Emerald 500
        "warning": "#F59E0B",  # Amber 500
        "error": "#EF4444",  # Red 500
        "info": "#3B82F6",  # Blue 500
        # Background Colors
        "background": "#0F172A",  # Slate 900
        "surface": "#1E293B",  # Slate 800
        "hover_surface": "#334155",  # Slate 700
        # Border Colors
        "border": "#475569",  # Slate 600
        "border_focus": "#818CF8",  # Indigo 400
        # Text Colors
        "text_primary": "#F1F5F9",  # Slate 100
        "text_secondary": "#94A3B8",  # Slate 400
        "text_disabled": "#64748B",  # Slate 500
        "text_on_primary": "#FFFFFF",  # White
        # Special
        "disabled": "#64748B",  # Slate 500
    }

    SAKURA_COLORS = {
        # Primary Colors
        "primary": "#D946A6",  # Sakura Pink (cherry blossom)
        "primary_hover": "#C7389F",  # Darker Sakura
        "secondary": "#7CB342",  # Bamboo Green
        # Status Colors
        "success": "#4CAF50",  # Natural Green
        "warning": "#FF9800",  # Autumn Orange
        "error": "#E53935",  # Red Torii
        "info": "#1976D2",  # Sky Blue
        # Background Colors
        "background": "#FFF8F5",  # Washi Paper White
        "surface": "#FFFBF7",  # Soft Cream
        "surface_alt": "#F5E6E8",  # Light Sakura
        "hover_surface": "#FFE9ED",  # Pale Sakura
        # Border Colors
        "border": "#E8D5D9",  # Soft Pink-Gray
        "border_accent": "#D946A6",  # Sakura Pink
        "border_focus": "#D946A6",  # Sakura Pink
        # Text Colors
        "text_primary": "#2C1810",  # Sumi Ink (traditional black ink)
        "text_secondary": "#8B7355",  # Tea Brown
        "text_disabled": "#C4B5A8",  # Faded Brown
        "text_on_primary": "#FFFFFF",  # White
        # Special/Decorative
        "decorative": "#FFB7C5",  # Cherry Blossom Petals
        "disabled": "#C4B5A8",  # Faded Brown
    }

    # Spacing, font sizes, border radius, and component specs are defined in
    # _variables.py as the single source of truth. They are substituted into
    # QSS files via ${var-name} syntax at load time.

    def __init__(self):
        """Initialize theme manager."""
        # Load saved theme preference
        settings = QSettings("AnkiMiner", "GUI")
        saved_theme = settings.value("theme", "light")
        if saved_theme in ["light", "dark", "sakura"]:
            self._current_mode = saved_theme

    @classmethod
    def get_instance(cls) -> "Theme":
        """Get or create the singleton Theme instance.

        Returns:
            The Theme singleton instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_current_mode(cls) -> ThemeMode:
        """Get the current theme mode.

        Returns:
            Current theme mode
        """
        instance = cls.get_instance()
        return instance._current_mode

    @classmethod
    def set_mode(cls, mode: ThemeMode) -> None:
        """Set the current theme mode and save preference.

        Args:
            mode: Theme mode to set ('light', 'dark', or 'sakura')
        """
        instance = cls.get_instance()
        instance._current_mode = mode

        # Save to settings
        settings = QSettings("AnkiMiner", "GUI")
        settings.setValue("theme", mode)

    @classmethod
    def get_colors(cls, mode: ThemeMode | None = None) -> dict[str, str]:
        """Get color palette for a theme mode.

        Args:
            mode: Theme mode, or None to use current mode

        Returns:
            Dictionary of color values
        """
        if mode is None:
            mode = cls.get_current_mode()

        color_map = {
            "light": cls.LIGHT_COLORS,
            "dark": cls.DARK_COLORS,
            "sakura": cls.SAKURA_COLORS,
        }
        return color_map.get(mode, cls.LIGHT_COLORS)

    @classmethod
    def get_stylesheet(cls, mode: ThemeMode | None = None) -> str:
        """Get the complete QSS stylesheet for a theme mode.

        Args:
            mode: Theme mode, or None to use current mode

        Returns:
            Complete QSS stylesheet as string
        """
        if mode is None:
            mode = cls.get_current_mode()

        # Get the styles directory path
        styles_dir = get_resource_dir() / "styles"

        # Load common styles
        common_qss = cls._load_qss_file(styles_dir / "common.qss")

        # Load theme-specific styles
        theme_file = f"{mode}_theme.qss"
        theme_qss = cls._load_qss_file(styles_dir / theme_file)

        # Combine stylesheets
        return common_qss + "\n\n" + theme_qss

    @classmethod
    def apply_to_app(cls, app: QApplication, mode: ThemeMode | None = None) -> None:
        """Apply theme stylesheet and palette to the application.

        Sets both QSS stylesheet and QPalette to ensure the theme background
        overrides the system theme (e.g. KDE dark) on all unstyled containers.

        Args:
            app: QApplication instance
            mode: Theme mode, or None to use current mode
        """
        if mode is None:
            mode = cls.get_current_mode()

        # Clear stylesheet first to force Qt to reset all widget styles,
        # preventing stale colors from the previous theme bleeding through.
        app.setStyleSheet("")

        # Build fresh palette with theme background to override KDE/system palette.
        colors = cls.get_colors(mode)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["background"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text_primary"]))
        app.setPalette(palette)

        # Apply stylesheet after palette so QSS takes precedence for styled widgets.
        app.setStyleSheet(cls.get_stylesheet(mode))

    @classmethod
    def _load_qss_file(cls, file_path: Path) -> str:
        """Load QSS file and perform variable substitution.

        Args:
            file_path: Path to QSS file

        Returns:
            QSS content with variables substituted
        """
        if not file_path.exists():
            return ""

        with open(file_path, encoding="utf-8") as f:
            qss_content = f.read()

        # Perform variable substitution for ${var} syntax
        qss_content = cls._substitute_variables(qss_content)

        return qss_content

    @classmethod
    def _substitute_variables(cls, qss_content: str) -> str:
        """Substitute ${variable-name} placeholders with actual values.

        Supports variables from _variables.py:
        - ${spacing-xs}, ${spacing-md}, etc.
        - ${font-size-h1}, ${font-size-body}, etc.
        - ${border-radius-small}, ${border-radius-large}, etc.

        Args:
            qss_content: QSS content with ${var} placeholders

        Returns:
            QSS content with variables replaced by values
        """
        variables = get_variable_dict()

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))

        return re.sub(r"\$\{([a-z0-9-]+)\}", replace_var, qss_content)

    @classmethod
    def cycle_theme(cls) -> ThemeMode:
        """Cycle to the next theme (light → dark → sakura → light).

        Returns:
            The new theme mode
        """
        current = cls.get_current_mode()

        if current == "light":
            new_mode: ThemeMode = "dark"
        elif current == "dark":
            new_mode = "sakura"
        else:  # sakura
            new_mode = "light"

        cls.set_mode(new_mode)
        return new_mode

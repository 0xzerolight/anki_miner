"""Theme management system for Anki Miner GUI.

This module provides centralized theme management with support for three themes:
- Light: Modern minimalist design with clean typography
- Dark: Comfortable dark palette for night usage
- Sakura: Culturally-inspired aesthetic with cherry blossom motifs
"""

import re
from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QSettings

from ._variables import get_variable_dict

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

    # ============== SPACING SYSTEM (8px base) ==============

    SPACING = {
        "xxs": 4,
        "xs": 8,
        "sm": 12,
        "md": 16,
        "lg": 24,
        "xl": 32,
        "xxl": 48,
    }

    # ============== TYPOGRAPHY ==============

    FONTS = {
        "family": "'Segoe UI', 'SF Pro Display', -apple-system, BlinkMacSystemFont, sans-serif",
        "family_japanese": "'Noto Sans JP', 'Yu Gothic', 'Meiryo', sans-serif",
        "family_mono": "'JetBrains Mono', 'Consolas', 'SF Mono', monospace",
        "sizes": {
            "h1": "24px",  # Main window title
            "h2": "20px",  # Tab titles, dialog headers
            "h3": "16px",  # Group boxes
            "body": "14px",  # Default
            "caption": "12px",  # Helper text
            "small": "11px",  # Status bar
        },
        "weights": {
            "regular": "400",
            "medium": "500",
            "semibold": "600",
            "bold": "700",
        },
        "line_heights": {
            "tight": "1.25",  # Headings
            "normal": "1.5",  # Body
            "relaxed": "1.75",  # Japanese text
        },
    }

    # ============== COMPONENT SPECS ==============

    COMPONENTS = {
        "border_radius": {
            "small": "4px",
            "default": "6px",
            "large": "8px",
        },
        "button": {
            "height": "36px",
            "padding_vertical": "10px",
            "padding_horizontal": "16px",
            "icon_size": "20px",
            "radius": "8px",
        },
        "input": {
            "height": "36px",
            "padding": "8px 12px",
            "radius": "6px",
        },
        "card": {
            "padding": "16px",
            "padding_large": "24px",
            "radius": "8px",
        },
        "progress": {
            "height": "8px",
            "radius": "4px",
        },
        "tab": {
            "height": "48px",
            "indicator_height": "3px",
        },
    }

    # ============== SHADOWS ==============

    SHADOWS = {
        "light": {
            "small": "0 1px 3px rgba(0, 0, 0, 0.1)",
            "medium": "0 4px 6px rgba(0, 0, 0, 0.1)",
            "large": "0 10px 15px rgba(0, 0, 0, 0.1)",
        },
        "dark": {
            "small": "0 1px 3px rgba(0, 0, 0, 0.3)",
            "medium": "0 4px 6px rgba(0, 0, 0, 0.3)",
            "large": "0 10px 15px rgba(0, 0, 0, 0.3)",
        },
        "sakura": {
            "small": "0 1px 2px rgba(44, 24, 16, 0.08)",
            "medium": "0 2px 4px rgba(44, 24, 16, 0.08)",
            "large": "0 4px 8px rgba(44, 24, 16, 0.08)",
        },
    }

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
        styles_dir = Path(__file__).parent

        # Load common styles
        common_qss = cls._load_qss_file(styles_dir / "common.qss")

        # Load theme-specific styles
        theme_file = f"{mode}_theme.qss"
        theme_qss = cls._load_qss_file(styles_dir / theme_file)

        # Combine stylesheets
        return common_qss + "\n\n" + theme_qss

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

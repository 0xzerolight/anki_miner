"""Theme management system for Anki Miner GUI."""

import json
import logging
import re
from pathlib import Path

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


class Theme:
    """Centralized theme management using JSON theme files.

    Discovers theme JSON files in the themes/ directory at startup.
    Each theme defines color tokens used for QSS variable substitution.
    """

    _instance: "Theme | None" = None
    _current_mode: str = "light"
    _themes: dict[str, dict] = {}

    def __init__(self) -> None:
        """Initialize theme manager: discover themes and load saved preference."""
        styles_dir = get_resource_dir() / "styles"
        themes_dir = styles_dir / "themes"
        self._themes = discover_themes(themes_dir)

        if not self._themes:
            raise RuntimeError(
                f"No valid theme files found in {themes_dir}. "
                "At least one valid JSON theme file is required."
            )

        # Load saved preference
        settings = QSettings("AnkiMiner", "GUI")
        saved_theme = settings.value("theme", "light")

        # Validate saved theme exists, fall back to first available
        if saved_theme in self._themes:
            self._current_mode = saved_theme
        else:
            self._current_mode = next(iter(self._themes))

    @classmethod
    def get_instance(cls) -> "Theme":
        """Get or create the singleton Theme instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_current_mode(cls) -> str:
        """Get the current theme mode (theme key, e.g. 'light', 'dark')."""
        instance = cls.get_instance()
        return instance._current_mode

    @classmethod
    def get_available_themes(cls) -> dict[str, str]:
        """Get available themes as {key: display_name}.

        Returns:
            Dict mapping theme key to display name, in discovery order.
        """
        instance = cls.get_instance()
        return {key: data["name"] for key, data in instance._themes.items()}

    @classmethod
    def set_mode(cls, mode: str) -> None:
        """Set the current theme mode and save preference.

        Args:
            mode: Theme key (e.g. 'light', 'dark', 'sakura')
        """
        instance = cls.get_instance()
        if mode not in instance._themes:
            logger.warning("Theme '%s' not found, keeping current theme", mode)
            return
        instance._current_mode = mode
        settings = QSettings("AnkiMiner", "GUI")
        settings.setValue("theme", mode)

    @classmethod
    def get_colors(cls, mode: str | None = None) -> dict[str, str]:
        """Get color palette for a theme mode.

        Args:
            mode: Theme key, or None for current mode

        Returns:
            Dictionary of color values
        """
        instance = cls.get_instance()
        if mode is None:
            mode = instance._current_mode
        theme_data = instance._themes.get(mode)
        if theme_data is None:
            theme_data = next(iter(instance._themes.values()))
        return theme_data["colors"]

    @classmethod
    def get_stylesheet(cls, mode: str | None = None) -> str:
        """Get the complete QSS stylesheet for a theme mode.

        Args:
            mode: Theme key, or None for current mode

        Returns:
            Complete QSS stylesheet with all variables substituted
        """
        instance = cls.get_instance()
        if mode is None:
            mode = instance._current_mode

        styles_dir = get_resource_dir() / "styles"
        common_qss = cls._load_qss_file(styles_dir / "common.qss", mode)
        return common_qss

    @classmethod
    def apply_to_app(cls, app: QApplication, mode: str | None = None) -> None:
        """Apply theme stylesheet and palette to the application.

        Args:
            app: QApplication instance
            mode: Theme key, or None for current mode
        """
        if mode is None:
            mode = cls.get_current_mode()

        app.setStyleSheet("")

        colors = cls.get_colors(mode)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["background"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        app.setPalette(palette)

        app.setStyleSheet(cls.get_stylesheet(mode))

    @classmethod
    def cycle_theme(cls) -> str:
        """Cycle to the next available theme.

        Returns:
            The new theme key
        """
        instance = cls.get_instance()
        keys = list(instance._themes.keys())
        try:
            current_index = keys.index(instance._current_mode)
            next_index = (current_index + 1) % len(keys)
        except ValueError:
            next_index = 0

        new_mode = keys[next_index]
        cls.set_mode(new_mode)
        return new_mode

    @classmethod
    def _load_qss_file(cls, file_path: Path, mode: str | None = None) -> str:
        """Load QSS file and perform variable substitution.

        Args:
            file_path: Path to QSS file
            mode: Theme key for color variable resolution

        Returns:
            QSS content with variables substituted
        """
        if not file_path.exists():
            return ""

        with open(file_path, encoding="utf-8") as f:
            qss_content = f.read()

        return cls._substitute_variables(qss_content, mode)

    @classmethod
    def _substitute_variables(cls, qss_content: str, mode: str | None = None) -> str:
        """Substitute ${variable-name} placeholders with actual values.

        Supports:
        - ${spacing-*}, ${font-size-*}, ${border-radius-*} from _variables.py
        - ${color-*} from the active theme JSON

        Args:
            qss_content: QSS content with ${var} placeholders
            mode: Theme key for color resolution

        Returns:
            QSS content with variables replaced
        """
        instance = cls.get_instance()
        if mode is None:
            mode = instance._current_mode

        # Combine layout variables with color variables
        variables = get_variable_dict()
        theme_data = instance._themes.get(mode)
        if theme_data:
            variables.update(get_color_variables(theme_data))

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))

        return re.sub(r"\$\{([a-z0-9-]+)\}", replace_var, qss_content)

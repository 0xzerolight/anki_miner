"""Theme management system for Anki Miner GUI."""

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

# Font scale bounds (inclusive).  Shared by initialize() and set_font_scale()
# so the two clamping sites can never drift apart.
FONT_SCALE_MIN: float = 0.5
FONT_SCALE_MAX: float = 2.0


def _clamp_font_scale(scale: float) -> float:
    """Return *scale* clamped to [FONT_SCALE_MIN, FONT_SCALE_MAX]."""
    return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, scale))


# Source marker for built-in themes shipped with the package.
SOURCE_SHIPPED = "shipped"
# Source marker for themes from ~/.anki_miner/themes/ (or wherever themes_root points).
SOURCE_USER = "user"


@dataclass(frozen=True)
class ThemeGroupEntry:
    """A single theme inside a grouped listing."""

    key: str
    variant_name: str
    display_name: str


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

    # Optional grouping fields. Missing is OK; present must be non-empty string.
    if "family" in data and (not isinstance(data["family"], str) or not data["family"].strip()):
        errors.append("'family' must be a non-empty string when present")
    if "variant" in data and (not isinstance(data["variant"], str) or not data["variant"].strip()):
        errors.append("'variant' must be a non-empty string when present")

    return errors


def _load_single_dir(themes_dir: Path, source: str) -> dict[str, dict]:
    """Load valid themes from one directory, tagging each with `_source`/`_path`.

    Returns dict keyed by file stem. Invalid or unreadable files are skipped
    with a warning.
    """
    out: dict[str, dict] = {}
    if not themes_dir.is_dir():
        return out

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

        data["_source"] = source
        data["_path"] = str(path)
        out[path.stem] = data

    return out


def discover_themes(themes_dirs: Path | Sequence[Path]) -> dict[str, dict]:
    """Discover and load valid theme JSON files from one or more directories.

    When multiple directories are provided, later directories override earlier
    ones on key collision (so a user-installed `dark.json` shadows the shipped
    one). Each theme dict carries `_source` (`"shipped"` or `"user"`) and
    `_path` (absolute path string) for UI use.

    Args:
        themes_dirs: A single directory or a sequence of directories. The
            first entry is treated as the shipped source; subsequent entries
            are treated as user sources.

    Returns:
        Dict of theme_key -> theme_data, sorted alphabetically within each
        source then merged (user wins on collision).
    """
    if isinstance(themes_dirs, Path):
        return _load_single_dir(themes_dirs, SOURCE_SHIPPED)

    themes: dict[str, dict] = {}
    for idx, themes_dir in enumerate(themes_dirs):
        source = SOURCE_SHIPPED if idx == 0 else SOURCE_USER
        loaded = _load_single_dir(themes_dir, source)
        themes.update(loaded)

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


# Callback signature: (active_theme_key, favorites_tuple) -> None
StateListener = Callable[[str, tuple[str, ...]], None]


class Theme:
    """Centralized theme management using JSON theme files.

    Discovers theme JSON files in the shipped themes/ directory at startup,
    optionally merging with a user themes directory. Persistence of the active
    theme and favorites list is owned by the caller (typically GUIConfigManager);
    Theme exposes a state-change callback so the caller can write through.
    """

    _instance: "Theme | None" = None
    _current_mode: str = "light"
    _favorites: tuple[str, ...] = ("light", "dark")
    _themes: dict[str, dict[str, Any]] = {}
    _user_dir: Path | None = None
    # When set, overrides the auto-detected shipped themes directory.
    # A non-existent path effectively disables shipped themes (used by tests).
    _shipped_dir_override: Path | None = None
    _state_listener: StateListener | None = None
    # Cache the raw common.qss bytes (shipped resource, never changes at
    # runtime) and the substituted output per theme mode. Without this,
    # previewing a theme re-read the 1183-line QSS file and re-ran a regex
    # substitution across it on every row click.
    _qss_template: str | None = None
    _compiled_qss: dict[str, str] = {}
    _font_scale: float = 1.0

    def __init__(self) -> None:
        """Discover themes from shipped + user dirs."""
        if self.__class__._shipped_dir_override is not None:
            shipped_dir = self.__class__._shipped_dir_override
        else:
            styles_dir = get_resource_dir() / "styles"
            shipped_dir = styles_dir / "themes"
        dirs: list[Path] = [shipped_dir]
        if self._user_dir is not None:
            dirs.append(self._user_dir)
        self._themes = discover_themes(dirs)

        if not self._themes:
            raise RuntimeError(
                f"No valid theme files found in {shipped_dir}. At least one valid JSON theme file is required."
            )

        # Validate active mode exists; fall back to first available.
        if self._current_mode not in self._themes:
            self._current_mode = next(iter(self._themes))

    @classmethod
    def initialize(
        cls,
        *,
        active: str = "light",
        favorites: tuple[str, ...] = ("light", "dark"),
        user_dir: Path | None = None,
        state_listener: StateListener | None = None,
        shipped_dir: Path | None = None,
        font_scale: float = 1.0,
    ) -> None:
        """Seed singleton state from external config and (re)discover themes.

        Call from app entry point after loading gui_config.json. Safe to call
        more than once — used by tests to reset between cases.

        Args:
            active: Active theme key.
            favorites: Ordered tuple of favorited theme keys.
            user_dir: Optional path to user themes directory (e.g.
                ``~/.anki_miner/themes``). Missing dir is tolerated.
            state_listener: Optional callback invoked with
                ``(active, favorites)`` after every state change. Used to
                persist to gui_config.json.
            shipped_dir: Override the shipped themes directory. When provided,
                replaces the auto-detected package shipped dir. A non-existent
                path disables shipped themes (useful for test isolation).
            font_scale: Global font scale multiplier for QSS font-size
                variables. Clamped to [0.5, 2.0]. Default 1.0 (no scaling).
        """
        cls._instance = None
        cls._current_mode = active
        cls._favorites = tuple(favorites)
        cls._user_dir = user_dir
        cls._shipped_dir_override = shipped_dir
        cls._state_listener = state_listener
        cls._font_scale = _clamp_font_scale(font_scale)
        # Theme JSONs may have changed (user dir swap, test reset); drop the
        # compiled-stylesheet cache so the next apply rebuilds from current
        # color values. The raw QSS template never changes at runtime, so
        # leave _qss_template alone.
        cls._compiled_qss = {}
        # Force re-discovery with new user_dir.
        cls.get_instance()

    @classmethod
    def get_instance(cls) -> "Theme":
        """Get or create the singleton Theme instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_current_mode(cls) -> str:
        """Get the current theme mode (theme key, e.g. 'light', 'dark')."""
        cls.get_instance()
        return cls._current_mode

    @classmethod
    def get_available_themes(cls) -> dict[str, str]:
        """Get all available themes as {key: display_name}.

        Returns:
            Dict mapping theme key to display name, in discovery order
            (shipped first, then user; user overrides shipped on collision).
        """
        instance = cls.get_instance()
        return {key: data["name"] for key, data in instance._themes.items()}

    @classmethod
    def get_themes_grouped(cls) -> list[tuple[str | None, list[ThemeGroupEntry]]]:
        """Return ordered list of (family | None, entries).

        Themes with a ``family`` field group under that family in their
        first-discovered position. Themes without ``family`` appear as
        standalone groups (family=None, single entry) at their discovery
        position.
        """
        instance = cls.get_instance()
        groups: list[tuple[str | None, list[ThemeGroupEntry]]] = []
        family_index: dict[str, int] = {}
        for key, data in instance._themes.items():
            display = str(data.get("name", key))
            family = data.get("family")
            variant_name = str(data.get("variant", display))
            entry = ThemeGroupEntry(key=key, variant_name=variant_name, display_name=display)
            if isinstance(family, str) and family.strip():
                idx = family_index.get(family)
                if idx is None:
                    family_index[family] = len(groups)
                    groups.append((family, [entry]))
                else:
                    groups[idx][1].append(entry)
            else:
                groups.append((None, [entry]))
        return groups

    @classmethod
    def get_favorites(cls) -> tuple[str, ...]:
        """Get the ordered favorites tuple as stored.

        May include keys for themes that have since been removed from disk;
        use :meth:`get_favorited_themes` for the filtered view used by UI.
        """
        cls.get_instance()
        return cls._favorites

    @classmethod
    def get_favorited_themes(cls) -> dict[str, str]:
        """Get favorited themes as {key: display_name}, filtered + ordered.

        Drops favorites whose theme is no longer discoverable. Preserves the
        order from the favorites tuple.
        """
        instance = cls.get_instance()
        return {key: instance._themes[key]["name"] for key in cls._favorites if key in instance._themes}

    @classmethod
    def is_favorite(cls, key: str) -> bool:
        """Return True if `key` is in the favorites list."""
        cls.get_instance()
        return key in cls._favorites

    @classmethod
    def set_mode(cls, mode: str) -> None:
        """Set the current theme mode and notify the state listener.

        Args:
            mode: Theme key (e.g. 'light', 'dark', 'sakura')
        """
        instance = cls.get_instance()
        if mode not in instance._themes:
            logger.warning("Theme '%s' not found, keeping current theme", mode)
            return
        if mode == cls._current_mode:
            return
        cls._current_mode = mode
        cls._notify_state_listener()

    @classmethod
    def get_font_scale(cls) -> float:
        """Return the current global font scale multiplier."""
        return cls._font_scale

    @classmethod
    def set_font_scale(cls, scale: float) -> None:
        """Set the global font scale multiplier and invalidate the QSS cache.

        Args:
            scale: New font scale. Clamped to [0.5, 2.0]. The compiled-QSS
                cache is cleared so the next get_stylesheet call recompiles
                with the updated scale. Callers are responsible for calling
                apply_to_app to repaint the live application.
        """
        clamped = _clamp_font_scale(scale)
        if clamped == cls._font_scale:
            return
        cls._font_scale = clamped
        cls._compiled_qss.clear()

    @classmethod
    def set_favorites(cls, favorites: Sequence[str]) -> None:
        """Replace the favorites list and notify the state listener.

        Unknown keys are silently dropped to keep stored state consistent
        with discovered themes.

        Args:
            favorites: New ordered favorites.
        """
        instance = cls.get_instance()
        filtered = tuple(k for k in favorites if k in instance._themes)
        if filtered == cls._favorites:
            return
        cls._favorites = filtered
        cls._notify_state_listener()

    @classmethod
    def add_favorite(cls, key: str) -> None:
        """Append `key` to favorites (if not present and the theme exists)."""
        if cls.is_favorite(key):
            return
        cls.set_favorites((*cls._favorites, key))

    @classmethod
    def remove_favorite(cls, key: str) -> None:
        """Remove `key` from favorites (no-op if not present)."""
        if not cls.is_favorite(key):
            return
        cls.set_favorites(tuple(k for k in cls._favorites if k != key))

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
            mode = cls._current_mode
        theme_data = instance._themes.get(mode)
        if theme_data is None:
            theme_data = next(iter(instance._themes.values()))
        colors: dict[str, str] = theme_data["colors"]
        return colors

    @classmethod
    def get_stylesheet(cls, mode: str | None = None) -> str:
        """Get the complete QSS stylesheet for a theme mode (cached per mode)."""
        cls.get_instance()
        if mode is None:
            mode = cls._current_mode

        cached = cls._compiled_qss.get(mode)
        if cached is not None:
            return cached

        if cls._qss_template is None:
            common_path = get_resource_dir() / "styles" / "common.qss"
            if common_path.exists():
                with open(common_path, encoding="utf-8") as f:
                    cls._qss_template = f.read()
            else:
                cls._qss_template = ""

        compiled = cls._substitute_variables(cls._qss_template, mode)
        cls._compiled_qss[mode] = compiled
        return compiled

    @classmethod
    def apply_to_app(cls, app: QApplication, mode: str | None = None) -> None:
        """Apply theme stylesheet and palette to the application."""
        if mode is None:
            mode = cls.get_current_mode()

        colors = cls.get_colors(mode)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["background"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface-alt"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        app.setPalette(palette)

        # One setStyleSheet call. The previous setStyleSheet("") clear forced
        # Qt to unpolish + re-polish the entire widget tree twice per apply.
        app.setStyleSheet(cls.get_stylesheet(mode))

    @classmethod
    def cycle_theme(cls) -> str:
        """Cycle to the next favorited theme.

        With one or zero favorites, this is a no-op and returns the current
        mode unchanged. Cycling deliberately ignores non-favorited themes —
        users curate the rotation via the Themes settings tab.

        Returns:
            The theme key after cycling (may equal the previous key).
        """
        favorites = cls.get_favorited_themes()
        keys = list(favorites.keys())

        if len(keys) < 2:
            # Nothing to cycle through.
            return cls._current_mode

        try:
            current_index = keys.index(cls._current_mode)
            next_index = (current_index + 1) % len(keys)
        except ValueError:
            # Active theme isn't in favorites — jump to first favorite.
            next_index = 0

        new_mode = keys[next_index]
        cls.set_mode(new_mode)
        return new_mode

    @classmethod
    def _notify_state_listener(cls) -> None:
        if cls._state_listener is None:
            return
        try:
            cls._state_listener(cls._current_mode, cls._favorites)
        except Exception:
            logger.exception("Theme state listener raised")

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
            mode = cls._current_mode

        variables = get_variable_dict(cls._font_scale)
        theme_data = instance._themes.get(mode)
        if theme_data:
            variables.update(get_color_variables(theme_data))

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))

        return re.sub(r"\$\{([a-z0-9-]+)\}", replace_var, qss_content)

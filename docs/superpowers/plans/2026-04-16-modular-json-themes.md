# Modular JSON Theme System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded theme color dicts and per-theme QSS files with auto-discovered JSON theme files, so adding a new theme = dropping one JSON file.

**Architecture:** Each theme is a single JSON file (45 color tokens) in `themes/` directory. `common.qss` uses `${color-*}` placeholders for all colors. `theme.py` discovers, validates, and loads JSON themes at runtime. Per-theme QSS files are deleted.

**Tech Stack:** Python 3.10+, PyQt6, JSON (stdlib), pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `anki_miner/gui/resources/styles/themes/light.json` | Light theme color tokens |
| Create | `anki_miner/gui/resources/styles/themes/dark.json` | Dark theme color tokens |
| Create | `anki_miner/gui/resources/styles/themes/sakura.json` | Sakura theme color tokens |
| Create | `tests/unit/test_theme_loader.py` | Unit tests for JSON loading/validation/discovery |
| Modify | `anki_miner/gui/resources/styles/theme.py` | Remove hardcoded dicts, add JSON discovery/loading/validation |
| Modify | `anki_miner/gui/resources/styles/common.qss` | Absorb color rules from theme QSS files as `${color-*}` vars |
| Modify | `anki_miner/gui/widgets/header_widget.py` | Populate theme combo from discovered themes |
| Modify | `anki_miner/gui/main_window.py` | Use dynamic theme list for cycling |
| Modify | `anki_miner/gui/constants.py` | Remove `THEME_ORDER` and `THEME_INDEX_MAP` |
| Delete | `anki_miner/gui/resources/styles/light_theme.qss` | Replaced by `themes/light.json` + `common.qss` |
| Delete | `anki_miner/gui/resources/styles/dark_theme.qss` | Replaced by `themes/dark.json` + `common.qss` |
| Delete | `anki_miner/gui/resources/styles/sakura_theme.qss` | Replaced by `themes/sakura.json` + `common.qss` |

---

### Task 1: Create JSON Theme Files

Extract colors from the three existing theme QSS files into JSON. Each color value comes directly from the corresponding hardcoded hex in the QSS.

**Files:**
- Create: `anki_miner/gui/resources/styles/themes/light.json`
- Create: `anki_miner/gui/resources/styles/themes/dark.json`
- Create: `anki_miner/gui/resources/styles/themes/sakura.json`

- [ ] **Step 1: Create `themes/` directory and `light.json`**

Run: `mkdir -p anki_miner/gui/resources/styles/themes`

Then create `anki_miner/gui/resources/styles/themes/light.json`:

```json
{
  "name": "Light",
  "author": "anki_miner",
  "colors": {
    "primary": "#6366F1",
    "primary-hover": "#4F46E5",
    "primary-pressed": "#4338CA",
    "primary-light": "#EEF2FF",
    "primary-dark": "#4F46E5",
    "secondary": "#8B5CF6",
    "background": "#F9FAFB",
    "surface": "#FFFFFF",
    "surface-hover": "#F3F4F6",
    "surface-alt": "#F9FAFB",
    "text": "#111827",
    "text-muted": "#6B7280",
    "text-disabled": "#9CA3AF",
    "text-on-primary": "#FFFFFF",
    "border": "#E5E7EB",
    "border-focus": "#6366F1",
    "border-subtle": "#D1D5DB",
    "disabled": "#9CA3AF",
    "input-bg": "#FFFFFF",
    "input-disabled-bg": "#F3F4F6",
    "error": "#EF4444",
    "error-hover": "#DC2626",
    "success": "#10B981",
    "warning": "#F59E0B",
    "info": "#3B82F6",
    "scrollbar": "#D1D5DB",
    "scrollbar-hover": "#9CA3AF",
    "tooltip-bg": "#1F2937",
    "tooltip-text": "#F3F4F6",
    "tooltip-border": "#374151",
    "divider": "#E5E7EB",
    "update-banner-bg": "#4338CA",
    "update-banner-text": "#FFFFFF",
    "decorative": "#E5E7EB",
    "badge-success-bg": "#D1FAE5",
    "badge-success-text": "#065F46",
    "badge-warning-bg": "#FEF3C7",
    "badge-warning-text": "#92400E",
    "badge-error-bg": "#FEE2E2",
    "badge-error-text": "#991B1B",
    "badge-info-bg": "#DBEAFE",
    "badge-info-text": "#1E40AF",
    "badge-pending-bg": "#F3F4F6",
    "badge-pending-text": "#6B7280",
    "table-selected-bg": "#EEF2FF",
    "table-selected-text": "#4F46E5"
  }
}
```

- [ ] **Step 2: Create `dark.json`**

Create `anki_miner/gui/resources/styles/themes/dark.json`:

```json
{
  "name": "Dark",
  "author": "anki_miner",
  "colors": {
    "primary": "#6366F1",
    "primary-hover": "#818CF8",
    "primary-pressed": "#4F46E5",
    "primary-light": "#312E81",
    "primary-dark": "#C7D2FE",
    "secondary": "#8B5CF6",
    "background": "#0F172A",
    "surface": "#1E293B",
    "surface-hover": "#334155",
    "surface-alt": "#0F172A",
    "text": "#F1F5F9",
    "text-muted": "#94A3B8",
    "text-disabled": "#64748B",
    "text-on-primary": "#FFFFFF",
    "border": "#475569",
    "border-focus": "#818CF8",
    "border-subtle": "#64748B",
    "disabled": "#64748B",
    "input-bg": "#1E293B",
    "input-disabled-bg": "#0F172A",
    "error": "#EF4444",
    "error-hover": "#F87171",
    "success": "#10B981",
    "warning": "#F59E0B",
    "info": "#3B82F6",
    "scrollbar": "#475569",
    "scrollbar-hover": "#64748B",
    "tooltip-bg": "#334155",
    "tooltip-text": "#F1F5F9",
    "tooltip-border": "#475569",
    "divider": "#475569",
    "update-banner-bg": "#312E81",
    "update-banner-text": "#E0E7FF",
    "decorative": "#475569",
    "badge-success-bg": "#064E3B",
    "badge-success-text": "#6EE7B7",
    "badge-warning-bg": "#78350F",
    "badge-warning-text": "#FCD34D",
    "badge-error-bg": "#7F1D1D",
    "badge-error-text": "#FCA5A5",
    "badge-info-bg": "#1E3A8A",
    "badge-info-text": "#93C5FD",
    "badge-pending-bg": "#334155",
    "badge-pending-text": "#94A3B8",
    "table-selected-bg": "#312E81",
    "table-selected-text": "#C7D2FE"
  }
}
```

- [ ] **Step 3: Create `sakura.json`**

Create `anki_miner/gui/resources/styles/themes/sakura.json`:

```json
{
  "name": "Sakura",
  "author": "anki_miner",
  "colors": {
    "primary": "#D946A6",
    "primary-hover": "#C7389F",
    "primary-pressed": "#B52A8F",
    "primary-light": "#FFE1E7",
    "primary-dark": "#B52A8F",
    "secondary": "#7CB342",
    "background": "#FFF8F5",
    "surface": "#FFFBF7",
    "surface-hover": "#FFE9ED",
    "surface-alt": "#F5E6E8",
    "text": "#2C1810",
    "text-muted": "#8B7355",
    "text-disabled": "#C4B5A8",
    "text-on-primary": "#FFFFFF",
    "border": "#E8D5D9",
    "border-focus": "#D946A6",
    "border-subtle": "#D9BFC5",
    "disabled": "#C4B5A8",
    "input-bg": "#FFFBF7",
    "input-disabled-bg": "#F5E6E8",
    "error": "#E53935",
    "error-hover": "#C62828",
    "success": "#4CAF50",
    "warning": "#FF9800",
    "info": "#1976D2",
    "scrollbar": "#E8D5D9",
    "scrollbar-hover": "#D9BFC5",
    "tooltip-bg": "#2C1810",
    "tooltip-text": "#FFFBF7",
    "tooltip-border": "#8B7355",
    "divider": "#FFB7C5",
    "update-banner-bg": "#BE185D",
    "update-banner-text": "#FFF1F2",
    "decorative": "#FFB7C5",
    "badge-success-bg": "#C8E6C9",
    "badge-success-text": "#1B5E20",
    "badge-warning-bg": "#FFE0B2",
    "badge-warning-text": "#E65100",
    "badge-error-bg": "#FFCDD2",
    "badge-error-text": "#B71C1C",
    "badge-info-bg": "#BBDEFB",
    "badge-info-text": "#0D47A1",
    "badge-pending-bg": "#E8D5D9",
    "badge-pending-text": "#8B7355",
    "table-selected-bg": "#FFE1E7",
    "table-selected-text": "#B52A8F"
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add anki_miner/gui/resources/styles/themes/
git commit -m "feat: add JSON theme files for light, dark, and sakura themes"
```

---

### Task 2: Write Theme Loader Tests

Write failing tests for the JSON theme loading, validation, and discovery logic before implementing it.

**Files:**
- Create: `tests/unit/test_theme_loader.py`

- [ ] **Step 1: Write test file with all theme loader tests**

Create `tests/unit/test_theme_loader.py`:

```python
"""Tests for JSON theme loading, validation, and discovery."""

import json
from pathlib import Path

import pytest


# The required color keys that every theme JSON must have
REQUIRED_COLOR_KEYS = [
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


def _make_valid_theme(name: str = "Test", author: str = "test") -> dict:
    """Create a minimal valid theme dict for testing."""
    return {
        "name": name,
        "author": author,
        "colors": {key: "#000000" for key in REQUIRED_COLOR_KEYS},
    }


@pytest.fixture
def themes_dir(tmp_path: Path) -> Path:
    """Create a temporary themes directory."""
    d = tmp_path / "themes"
    d.mkdir()
    return d


@pytest.fixture
def valid_theme_file(themes_dir: Path) -> Path:
    """Write a valid theme JSON to the temp themes directory."""
    theme = _make_valid_theme("ValidTheme")
    path = themes_dir / "valid.json"
    path.write_text(json.dumps(theme))
    return path


class TestValidateTheme:
    """Tests for validate_theme_data()."""

    def test_valid_theme_passes(self):
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        theme = _make_valid_theme()
        errors = validate_theme_data(theme)
        assert errors == []

    def test_missing_name_field(self):
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        theme = _make_valid_theme()
        del theme["name"]
        errors = validate_theme_data(theme)
        assert any("name" in e for e in errors)

    def test_missing_colors_field(self):
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        theme = _make_valid_theme()
        del theme["colors"]
        errors = validate_theme_data(theme)
        assert any("colors" in e for e in errors)

    def test_missing_color_key(self):
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        theme = _make_valid_theme()
        del theme["colors"]["primary"]
        errors = validate_theme_data(theme)
        assert any("primary" in e for e in errors)

    def test_name_not_string(self):
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        theme = _make_valid_theme()
        theme["name"] = 123
        errors = validate_theme_data(theme)
        assert any("name" in e for e in errors)

    def test_extra_color_keys_ok(self):
        """Extra keys should not cause validation errors."""
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        theme = _make_valid_theme()
        theme["colors"]["custom-accent"] = "#FF0000"
        errors = validate_theme_data(theme)
        assert errors == []


class TestDiscoverThemes:
    """Tests for discover_themes()."""

    def test_discovers_valid_json_files(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        # Write two valid themes
        for name in ["alpha", "beta"]:
            theme = _make_valid_theme(name.capitalize())
            (themes_dir / f"{name}.json").write_text(json.dumps(theme))

        themes = discover_themes(themes_dir)
        assert len(themes) == 2
        # Sorted alphabetically by filename
        names = [t["name"] for t in themes.values()]
        assert names == ["Alpha", "Beta"]

    def test_skips_invalid_json(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        # Valid theme
        valid = _make_valid_theme("Good")
        (themes_dir / "good.json").write_text(json.dumps(valid))

        # Invalid JSON (not parseable)
        (themes_dir / "bad.json").write_text("{not valid json")

        themes = discover_themes(themes_dir)
        assert len(themes) == 1
        assert "good" in themes

    def test_skips_theme_with_missing_keys(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        # Valid theme
        valid = _make_valid_theme("Good")
        (themes_dir / "good.json").write_text(json.dumps(valid))

        # Theme missing required color key
        bad = _make_valid_theme("Bad")
        del bad["colors"]["primary"]
        (themes_dir / "bad.json").write_text(json.dumps(bad))

        themes = discover_themes(themes_dir)
        assert len(themes) == 1

    def test_empty_directory(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        themes = discover_themes(themes_dir)
        assert themes == {}

    def test_ignores_non_json_files(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        valid = _make_valid_theme("Good")
        (themes_dir / "good.json").write_text(json.dumps(valid))
        (themes_dir / "readme.txt").write_text("not a theme")

        themes = discover_themes(themes_dir)
        assert len(themes) == 1

    def test_theme_key_is_stem(self, themes_dir: Path):
        """Theme dict key should be the filename stem (no extension)."""
        from anki_miner.gui.resources.styles.theme import discover_themes

        valid = _make_valid_theme("My Theme")
        (themes_dir / "my-theme.json").write_text(json.dumps(valid))

        themes = discover_themes(themes_dir)
        assert "my-theme" in themes


class TestGetColorVariables:
    """Tests for getting color variables from a theme for QSS substitution."""

    def test_color_vars_prefixed(self):
        from anki_miner.gui.resources.styles.theme import get_color_variables

        theme = _make_valid_theme()
        theme["colors"]["primary"] = "#FF0000"
        variables = get_color_variables(theme)
        assert variables["color-primary"] == "#FF0000"

    def test_all_required_keys_present(self):
        from anki_miner.gui.resources.styles.theme import get_color_variables

        theme = _make_valid_theme()
        variables = get_color_variables(theme)
        for key in REQUIRED_COLOR_KEYS:
            assert f"color-{key}" in variables


class TestBuiltinThemeFiles:
    """Tests that the shipped JSON theme files are valid."""

    @pytest.mark.parametrize("theme_name", ["light", "dark", "sakura"])
    def test_builtin_theme_valid(self, theme_name: str):
        from anki_miner.gui.resources.styles.theme import validate_theme_data

        themes_dir = (
            Path(__file__).parent.parent.parent
            / "anki_miner"
            / "gui"
            / "resources"
            / "styles"
            / "themes"
        )
        theme_path = themes_dir / f"{theme_name}.json"
        assert theme_path.exists(), f"{theme_name}.json not found"

        with open(theme_path) as f:
            data = json.load(f)

        errors = validate_theme_data(data)
        assert errors == [], f"{theme_name}.json validation errors: {errors}"

    @pytest.mark.parametrize("theme_name", ["light", "dark", "sakura"])
    def test_builtin_theme_has_45_colors(self, theme_name: str):
        themes_dir = (
            Path(__file__).parent.parent.parent
            / "anki_miner"
            / "gui"
            / "resources"
            / "styles"
            / "themes"
        )
        theme_path = themes_dir / f"{theme_name}.json"

        with open(theme_path) as f:
            data = json.load(f)

        assert len(data["colors"]) == 45
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_theme_loader.py -v`

Expected: ImportError failures — `validate_theme_data`, `discover_themes`, `get_color_variables` don't exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_theme_loader.py
git commit -m "test: add failing tests for JSON theme loader"
```

---

### Task 3: Implement Theme Loader Functions in `theme.py`

Add `validate_theme_data()`, `discover_themes()`, and `get_color_variables()` functions. Keep the existing `Theme` class working — we'll refactor it to use these in Task 5.

**Files:**
- Modify: `anki_miner/gui/resources/styles/theme.py`

- [ ] **Step 1: Add the required color keys constant and three new functions**

Add at the top of `theme.py`, after the existing imports:

```python
import json
import logging

logger = logging.getLogger(__name__)
```

Add the following after the imports and before the `Theme` class (around line 21):

```python
REQUIRED_COLOR_KEYS = frozenset([
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
])


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
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/unit/test_theme_loader.py -v`

Expected: All tests pass except `TestBuiltinThemeFiles` (theme JSON files created in Task 1 should already exist — these should pass too). If Task 1 was completed, all tests pass.

- [ ] **Step 3: Commit**

```bash
git add anki_miner/gui/resources/styles/theme.py
git commit -m "feat: add theme validation, discovery, and color variable extraction"
```

---

### Task 4: Merge Color Rules Into `common.qss`

Replace every hardcoded color hex in the three theme QSS files with `${color-*}` variables in `common.qss`. After this, `common.qss` contains ALL styling rules (structural + color).

**Files:**
- Modify: `anki_miner/gui/resources/styles/common.qss`

- [ ] **Step 1: Append all color rules to `common.qss`**

Add the following to the end of `common.qss`. Every hex value is now a `${color-*}` variable. These rules were extracted from `light_theme.qss`, `dark_theme.qss`, and `sakura_theme.qss` — using the union of all selectors.

```css
/* ============== THEME COLOR RULES ============== */
/* All colors use ${color-*} variables from the active JSON theme file. */

/* --- Base Colors --- */

QWidget {
    color: ${color-text};
}

QMainWindow {
    background-color: ${color-background};
}

QScrollArea {
    background-color: ${color-background};
}

QScrollArea > QWidget {
    background-color: ${color-background};
}

/* --- Buttons --- */

QPushButton {
    background-color: ${color-primary};
    color: ${color-text-on-primary};
}

QPushButton:hover {
    background-color: ${color-primary-hover};
}

QPushButton:pressed {
    background-color: ${color-primary-pressed};
}

QPushButton:disabled {
    background-color: ${color-disabled};
    color: ${color-text-on-primary};
}

QPushButton#secondary {
    background-color: transparent;
    color: ${color-primary};
    border-color: ${color-primary};
}

QPushButton#secondary:hover {
    background-color: ${color-primary-light};
    border-color: ${color-primary-hover};
}

QPushButton#ghost {
    color: ${color-text};
}

QPushButton#ghost:hover {
    background-color: ${color-surface-hover};
}

QPushButton#danger {
    background-color: ${color-error};
    color: ${color-text-on-primary};
}

QPushButton#danger:hover {
    background-color: ${color-error-hover};
}

/* --- Input Fields --- */

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: ${color-input-bg};
    color: ${color-text};
    border-color: ${color-border};
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: ${color-border-focus};
    background-color: ${color-input-bg};
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background-color: ${color-input-disabled-bg};
    color: ${color-text-disabled};
}

QLineEdit::placeholder {
    color: ${color-text-muted};
}

/* --- Group Boxes --- */

QGroupBox {
    color: ${color-text};
    background-color: ${color-surface};
    border-color: ${color-border};
}

QGroupBox::title {
    color: ${color-text};
}

/* --- Tabs --- */

QTabWidget::pane {
    background-color: ${color-background};
    border: none;
}

QTabBar::tab {
    color: ${color-text-muted};
    background-color: transparent;
}

QTabBar::tab:hover {
    color: ${color-text};
    background-color: ${color-surface-hover};
}

QTabBar::tab:selected {
    color: ${color-primary};
    background-color: transparent;
    border-bottom: 3px solid ${color-primary};
}

/* --- Text Edit / Log --- */

QTextEdit {
    background-color: ${color-surface};
    color: ${color-text};
    border-color: ${color-border};
}

QTextEdit#log-widget {
    background-color: ${color-surface};
}

/* --- Progress Bar --- */

QProgressBar {
    background-color: ${color-border};
    color: ${color-text};
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 ${color-primary}, stop:1 ${color-secondary});
}

/* --- Tables --- */

QTableWidget {
    background-color: ${color-surface};
    color: ${color-text};
    border-color: ${color-border};
}

QTableWidget::item:hover {
    background-color: ${color-surface-hover};
}

QTableWidget::item:selected {
    background-color: ${color-table-selected-bg};
    color: ${color-table-selected-text};
}

QHeaderView::section {
    background-color: ${color-background};
    color: ${color-text};
    border-color: ${color-border};
}

/* --- Scroll Bars --- */

QScrollBar:vertical, QScrollBar:horizontal {
    background-color: transparent;
}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: ${color-scrollbar};
}

QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background-color: ${color-scrollbar-hover};
}

/* --- Status Bar --- */

QStatusBar {
    background-color: ${color-surface};
    color: ${color-text-muted};
    border-color: ${color-border};
}

/* --- Tooltips --- */

QToolTip {
    background-color: ${color-tooltip-bg};
    color: ${color-tooltip-text};
    border-color: ${color-tooltip-border};
}

/* --- Menus --- */

QMenu {
    background-color: ${color-surface};
    color: ${color-text};
    border-color: ${color-border};
}

QMenu::item:selected {
    background-color: ${color-surface-hover};
    color: ${color-text};
}

QMenu::separator {
    background-color: ${color-border};
}

/* --- Combo Box Dropdown --- */

QComboBox QAbstractItemView {
    background-color: ${color-surface};
    color: ${color-text};
    border-color: ${color-border};
}

QComboBox QAbstractItemView::item:hover {
    background-color: ${color-surface-hover};
}

QComboBox QAbstractItemView::item:selected {
    background-color: ${color-table-selected-bg};
    color: ${color-table-selected-text};
}

/* --- Check Boxes & Radio Buttons --- */

QCheckBox, QRadioButton {
    color: ${color-text};
}

QCheckBox::indicator, QRadioButton::indicator {
    background-color: ${color-input-bg};
    border-color: ${color-border-subtle};
}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: ${color-primary};
}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: ${color-primary};
    border-color: ${color-primary};
}

/* --- Dialogs --- */

QDialog {
    background-color: ${color-surface};
    color: ${color-text};
}

QMessageBox {
    background-color: ${color-surface};
}

/* --- Custom Widgets --- */

/* Log header */
QWidget#log-header {
    background-color: ${color-surface-alt};
    border-bottom: 1px solid ${color-border};
}

/* Status badges */
QLabel#status-badge {
    background-color: ${color-badge-pending-bg};
    color: ${color-badge-pending-text};
    border-color: ${color-border-subtle};
}

QLabel#status-badge[status="success"] {
    background-color: ${color-badge-success-bg};
    color: ${color-badge-success-text};
    border-color: ${color-success};
}

QLabel#status-badge[status="warning"] {
    background-color: ${color-badge-warning-bg};
    color: ${color-badge-warning-text};
}

QLabel#status-badge[status="error"] {
    background-color: ${color-badge-error-bg};
    color: ${color-badge-error-text};
    border-color: ${color-error};
}

QLabel#status-badge[status="info"],
QLabel#status-badge[status="checking"] {
    background-color: ${color-badge-info-bg};
    color: ${color-badge-info-text};
    border-color: ${color-info};
}

QLabel#status-badge[status="pending"] {
    background-color: ${color-badge-pending-bg};
    color: ${color-badge-pending-text};
}

/* Stat cards */
QFrame#stat-card {
    background-color: ${color-surface};
    border-color: ${color-border};
}

QLabel#stat-value {
    color: ${color-primary};
}

QLabel#stat-label {
    color: ${color-text-muted};
}

/* Section headers */
QLabel#section-header {
    color: ${color-text};
}

/* Frames and cards */
QFrame#card {
    background-color: ${color-surface};
    border-color: ${color-border};
}

QFrame#divider {
    background-color: ${color-divider};
}

/* Progress stats */
QLabel#progress-stats {
    color: ${color-text-muted};
}

/* --- Special States --- */

QLineEdit[error="true"] {
    border-color: ${color-error};
    border-width: 2px;
}

QLineEdit[success="true"] {
    border-color: ${color-success};
    border-width: 2px;
}

QLineEdit[warning="true"] {
    border-color: ${color-warning};
    border-width: 2px;
}

/* --- Validation Badges --- */

QLabel#validation-badge {
    border: 1px solid ${color-border};
}

QLabel#validation-badge[status="checking"] {
    background-color: ${color-badge-pending-bg};
    color: ${color-badge-pending-text};
    border-color: ${color-border};
}

QLabel#validation-badge[status="success"] {
    background-color: ${color-badge-success-bg};
    color: ${color-badge-success-text};
    border-color: ${color-success};
}

QLabel#validation-badge[status="error"] {
    background-color: ${color-badge-error-bg};
    color: ${color-badge-error-text};
    border-color: ${color-error};
}

QLabel#validation-badge[status="warning"] {
    background-color: ${color-badge-warning-bg};
    color: ${color-badge-warning-text};
    border-color: ${color-warning};
}

/* --- Header Widget --- */

QWidget#header-widget {
    background-color: ${color-surface};
    border-color: ${color-border};
}

/* --- Update Banner --- */

QFrame#update-banner {
    background-color: ${color-update-banner-bg};
}

QFrame#update-banner QLabel {
    color: ${color-update-banner-text};
}

QFrame#update-banner QPushButton {
    color: ${color-update-banner-text};
    border-color: rgba(255, 255, 255, 0.4);
}

QFrame#update-banner QPushButton:hover {
    background-color: rgba(255, 255, 255, 0.2);
}

/* --- Status Bar Widget --- */

QWidget#status-bar {
    background-color: ${color-surface};
    border-top: 1px solid ${color-border};
}

QLabel#status-operation[level="info"] {
    color: ${color-info};
}

QLabel#status-operation[level="success"] {
    color: ${color-success};
}

QLabel#status-operation[level="warning"] {
    color: ${color-warning};
}

QLabel#status-operation[level="error"] {
    color: ${color-error};
}

QLabel#status-stats {
    color: ${color-text-muted};
}

QLabel#status-indicator[status="success"] {
    background-color: ${color-badge-success-bg};
    color: ${color-badge-success-text};
}

QLabel#status-indicator[status="error"] {
    background-color: ${color-badge-error-bg};
    color: ${color-badge-error-text};
}

QFrame#status-separator {
    background-color: ${color-border};
}

/* --- Queue Item Card --- */

QFrame#queue-item-card {
    background-color: ${color-surface};
    border-color: ${color-border};
}

QFrame#queue-item-card:hover {
    border-color: ${color-primary};
    background-color: ${color-surface-hover};
}

QLabel#queue-item-title {
    color: ${color-text};
}

QLabel#queue-item-path {
    color: ${color-text-muted};
}

QLabel#queue-item-stats {
    color: ${color-primary};
}

QLabel#queue-stats {
    background-color: ${color-primary-light};
    color: ${color-primary-dark};
}

QLabel#queue-status-badge[status="pending"] {
    background-color: ${color-badge-pending-bg};
    color: ${color-badge-pending-text};
}

QLabel#queue-status-badge[status="processing"] {
    background-color: ${color-badge-info-bg};
    color: ${color-badge-info-text};
}

QLabel#queue-status-badge[status="complete"] {
    background-color: ${color-badge-success-bg};
    color: ${color-badge-success-text};
}

QProgressBar#queue-progress {
    background-color: ${color-border};
}

QProgressBar#queue-progress::chunk {
    background-color: ${color-primary};
}

/* --- Settings Tab --- */

QLabel#validation-status[status="success"] {
    color: ${color-success};
}

QLabel#validation-status[status="error"] {
    color: ${color-error};
}

QLabel#validation-status[status="checking"] {
    color: ${color-info};
}

QLabel#helper-text {
    color: ${color-text-muted};
}

QTabWidget#settings-tabs QTabBar::tab {
    background-color: transparent;
    color: ${color-text-muted};
    border-bottom: 2px solid transparent;
}

QTabWidget#settings-tabs QTabBar::tab:selected {
    background-color: ${color-background};
    color: ${color-text};
    border-bottom: 2px solid ${color-primary};
}

QTabWidget#settings-tabs QTabBar::tab:hover {
    background-color: ${color-surface-hover};
    color: ${color-text};
}

/* --- Sakura-specific: Decorative elements --- */

QLabel#decorative {
    color: ${color-decorative};
}

QGroupBox#accent {
    border: 2px solid;
    border-color: ${color-primary};
}
```

- [ ] **Step 2: Commit**

```bash
git add anki_miner/gui/resources/styles/common.qss
git commit -m "feat: add color variable rules to common.qss"
```

---

### Task 5: Refactor `theme.py` to Use JSON Themes

Replace the hardcoded color dicts and theme QSS loading with JSON-based discovery. This is the core refactor.

**Files:**
- Modify: `anki_miner/gui/resources/styles/theme.py`

- [ ] **Step 1: Rewrite the `Theme` class**

Replace the entire `Theme` class (keep the imports and the new functions from Task 3) with:

```python
class Theme:
    """Centralized theme management using JSON theme files.

    Discovers theme JSON files in the themes/ directory at startup.
    Each theme defines 45 color tokens used for QSS variable substitution.
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
```

Also remove the old `ThemeMode` type alias and the three `*_COLORS` class dicts — they're fully replaced.

The final import block at the top of the file should be:

```python
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
```

- [ ] **Step 2: Run all theme tests**

Run: `pytest tests/unit/test_theme_loader.py -v`

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add anki_miner/gui/resources/styles/theme.py
git commit -m "refactor: rewrite Theme class to use JSON theme discovery"
```

---

### Task 6: Update Header Widget and Main Window

Replace hardcoded theme lists with dynamic discovery from `Theme.get_available_themes()`.

**Files:**
- Modify: `anki_miner/gui/widgets/header_widget.py:73-81`
- Modify: `anki_miner/gui/widgets/header_widget.py:115-123`
- Modify: `anki_miner/gui/main_window.py:184-194`
- Modify: `anki_miner/gui/constants.py:19-20`

- [ ] **Step 1: Update `header_widget.py` — populate combo from discovered themes**

In `header_widget.py`, replace the hardcoded combo box population (lines 73-81):

```python
        # OLD:
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Sakura", "sakura")

        # Set current theme
        current_theme = Theme.get_current_mode()
        theme_index = {"light": 0, "dark": 1, "sakura": 2}.get(current_theme, 0)
        self.theme_combo.setCurrentIndex(theme_index)
```

With:

```python
        # Populate from discovered themes
        available = Theme.get_available_themes()
        for key, display_name in available.items():
            self.theme_combo.addItem(display_name, key)

        # Set current theme
        current_theme = Theme.get_current_mode()
        keys = list(available.keys())
        theme_index = keys.index(current_theme) if current_theme in keys else 0
        self.theme_combo.setCurrentIndex(theme_index)
```

Also update `update_theme_selector()` (lines 115-123):

```python
    def update_theme_selector(self) -> None:
        """Update theme selector to match current theme."""
        current_theme = Theme.get_current_mode()

        # Find index by item data
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == current_theme:
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(i)
                self.theme_combo.blockSignals(False)
                return
```

Update the tooltip (line 85) to be dynamic:

```python
        theme_names = ", ".join(Theme.get_available_themes().values())
        self.theme_combo.setToolTip(
            f"Select application theme: {theme_names} (Ctrl+T to cycle)"
        )
```

- [ ] **Step 2: Update `main_window.py` — remove `THEME_ORDER` usage**

In `main_window.py`, replace the `_cycle_theme` method (lines 184-194):

```python
    def _cycle_theme(self) -> None:
        """Cycle through available themes."""
        new_mode = Theme.cycle_theme()

        # Update combo box to reflect the new theme
        self.header.update_theme_selector()

        # Apply theme
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app, new_mode)
```

Remove `THEME_ORDER` from the import on line 10. The import line currently reads:

```python
from anki_miner.gui.constants import (
    ...
    THEME_ORDER,
    ...
)
```

Remove `THEME_ORDER,` from that import block.

- [ ] **Step 3: Update `constants.py` — remove theme constants**

In `anki_miner/gui/constants.py`, remove lines 18-20:

```python
# Remove these lines:
THEME_ORDER = ["light", "dark", "sakura"]
THEME_INDEX_MAP = {"light": 0, "dark": 1, "sakura": 2}
```

Check if `THEME_INDEX_MAP` is imported anywhere:

Run: `grep -r "THEME_INDEX_MAP" anki_miner/`

If used elsewhere, remove those imports too.

- [ ] **Step 4: Run full test suite**

Run: `pytest -x -v`

Expected: All tests pass. No imports reference `THEME_ORDER` or `THEME_INDEX_MAP` anymore.

- [ ] **Step 5: Commit**

```bash
git add anki_miner/gui/widgets/header_widget.py anki_miner/gui/main_window.py anki_miner/gui/constants.py
git commit -m "refactor: use dynamic theme discovery in header widget and main window"
```

---

### Task 7: Delete Old Theme QSS Files

Remove the per-theme QSS files that are now fully replaced by JSON + `common.qss`.

**Files:**
- Delete: `anki_miner/gui/resources/styles/light_theme.qss`
- Delete: `anki_miner/gui/resources/styles/dark_theme.qss`
- Delete: `anki_miner/gui/resources/styles/sakura_theme.qss`

- [ ] **Step 1: Delete the files**

```bash
git rm anki_miner/gui/resources/styles/light_theme.qss
git rm anki_miner/gui/resources/styles/dark_theme.qss
git rm anki_miner/gui/resources/styles/sakura_theme.qss
```

- [ ] **Step 2: Verify no remaining references**

Run: `grep -r "light_theme\|dark_theme\|sakura_theme" anki_miner/`

If any references remain, fix them (they shouldn't — `get_stylesheet()` in Task 5 no longer loads theme-specific QSS).

- [ ] **Step 3: Run full test suite**

Run: `pytest -x -v`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: delete per-theme QSS files replaced by JSON themes"
```

---

### Task 8: Manual Verification

Launch the GUI and verify all three themes render correctly.

- [ ] **Step 1: Launch the GUI**

Run: `python -m anki_miner_gui` (or however the GUI entry point works)

- [ ] **Step 2: Verify each theme**

For each theme (Light, Dark, Sakura), switch to it via the combo box and check:
- Background colors correct
- Button colors (primary, secondary, ghost, danger) correct
- Input field styling (normal, focused, disabled)
- Tab bar colors
- Status badges (if visible)
- Scroll bars
- Tooltips (hover over elements)
- Theme cycling via Ctrl+T works

- [ ] **Step 3: Verify theme persistence**

1. Select Dark theme
2. Close the app
3. Relaunch — should start in Dark theme

- [ ] **Step 4: Run full quality checks**

```bash
ruff check anki_miner/gui/resources/styles/
mypy anki_miner/gui/resources/styles/
pytest -x -v
```

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: theme rendering adjustments from manual verification"
```

Only commit if there were actual fixes. Skip if everything was clean.

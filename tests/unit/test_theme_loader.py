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
        "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000"),
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

        for name in ["alpha", "beta"]:
            theme = _make_valid_theme(name.capitalize())
            (themes_dir / f"{name}.json").write_text(json.dumps(theme))

        themes = discover_themes(themes_dir)
        assert len(themes) == 2
        names = [t["name"] for t in themes.values()]
        assert names == ["Alpha", "Beta"]

    def test_skips_invalid_json(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        valid = _make_valid_theme("Good")
        (themes_dir / "good.json").write_text(json.dumps(valid))
        (themes_dir / "bad.json").write_text("{not valid json")

        themes = discover_themes(themes_dir)
        assert len(themes) == 1
        assert "good" in themes

    def test_skips_theme_with_missing_keys(self, themes_dir: Path):
        from anki_miner.gui.resources.styles.theme import discover_themes

        valid = _make_valid_theme("Good")
        (themes_dir / "good.json").write_text(json.dumps(valid))

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

        themes_dir = Path(__file__).parent.parent.parent / "anki_miner" / "gui" / "resources" / "styles" / "themes"
        theme_path = themes_dir / f"{theme_name}.json"
        assert theme_path.exists(), f"{theme_name}.json not found"

        with open(theme_path) as f:
            data = json.load(f)

        errors = validate_theme_data(data)
        assert errors == [], f"{theme_name}.json validation errors: {errors}"

    @pytest.mark.parametrize("theme_name", ["light", "dark", "sakura"])
    def test_builtin_theme_has_45_colors(self, theme_name: str):
        themes_dir = Path(__file__).parent.parent.parent / "anki_miner" / "gui" / "resources" / "styles" / "themes"
        theme_path = themes_dir / f"{theme_name}.json"

        with open(theme_path) as f:
            data = json.load(f)

        assert len(data["colors"]) == 46

"""Tests for optional family/variant fields on theme JSON + grouping helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.gui.resources.styles.theme import (
    REQUIRED_COLOR_KEYS,
    Theme,
    validate_theme_data,
)


def _make_theme_dict(**overrides: object) -> dict[str, object]:
    """Return a minimal valid theme dict, parameterizable by overrides."""
    colors = dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000")
    data: dict[str, object] = {"name": "Test", "colors": colors}
    data.update(overrides)
    return data


class TestValidateThemeData:
    def test_optional_family_accepted_when_string(self) -> None:
        errors = validate_theme_data(_make_theme_dict(family="Catppuccin"))
        assert errors == []

    def test_optional_variant_accepted_when_string(self) -> None:
        errors = validate_theme_data(_make_theme_dict(family="Catppuccin", variant="Mocha"))
        assert errors == []

    def test_family_missing_is_valid(self) -> None:
        errors = validate_theme_data(_make_theme_dict())
        assert errors == []

    def test_family_non_string_is_error(self) -> None:
        errors = validate_theme_data(_make_theme_dict(family=123))
        assert any("family" in e for e in errors)

    def test_family_empty_string_is_error(self) -> None:
        errors = validate_theme_data(_make_theme_dict(family=""))
        assert any("family" in e for e in errors)

    def test_variant_non_string_is_error(self) -> None:
        errors = validate_theme_data(_make_theme_dict(variant=42))
        assert any("variant" in e for e in errors)

    def test_family_whitespace_only_is_error(self) -> None:
        errors = validate_theme_data(_make_theme_dict(family="   "))
        assert any("family" in e for e in errors)

    def test_variant_empty_string_is_error(self) -> None:
        errors = validate_theme_data(_make_theme_dict(variant=""))
        assert any("variant" in e for e in errors)

    def test_variant_whitespace_only_is_error(self) -> None:
        errors = validate_theme_data(_make_theme_dict(variant="   "))
        assert any("variant" in e for e in errors)

    def test_variant_alone_without_family_is_valid(self) -> None:
        errors = validate_theme_data(_make_theme_dict(variant="Mocha"))
        assert errors == []


def _write_theme_file(path: Path, key: str, **overrides: object) -> None:
    data = _make_theme_dict(**overrides)
    data["name"] = overrides.get("name", key.replace("-", " ").title())
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def themes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "themes"
    d.mkdir()
    return d


class TestGetThemesGrouped:
    def _init_with(self, themes_dir: Path, active: str = "light") -> None:
        """Force Theme singleton to read from a temp directory."""
        Theme.initialize(active=active, favorites=(), user_dir=themes_dir, shipped_dir=themes_dir)

    def test_standalone_theme_yields_none_family(self, themes_dir: Path) -> None:
        _write_theme_file(themes_dir / "light.json", "light", name="Light")
        self._init_with(themes_dir)
        groups = Theme.get_themes_grouped()
        assert len(groups) == 1
        family, entries = groups[0]
        assert family is None
        assert len(entries) == 1
        assert entries[0].key == "light"
        assert entries[0].display_name == "Light"

    def test_family_groups_variants_together(self, themes_dir: Path) -> None:
        _write_theme_file(
            themes_dir / "catppuccin-mocha.json",
            "catppuccin-mocha",
            name="Catppuccin Mocha",
            family="Catppuccin",
            variant="Mocha",
        )
        _write_theme_file(
            themes_dir / "catppuccin-latte.json",
            "catppuccin-latte",
            name="Catppuccin Latte",
            family="Catppuccin",
            variant="Latte",
        )
        self._init_with(themes_dir, active="catppuccin-mocha")
        groups = Theme.get_themes_grouped()
        assert len(groups) == 1
        family, entries = groups[0]
        assert family == "Catppuccin"
        keys = {e.key for e in entries}
        assert keys == {"catppuccin-mocha", "catppuccin-latte"}

    def test_variant_falls_back_to_name_when_missing(self, themes_dir: Path) -> None:
        _write_theme_file(
            themes_dir / "ctp.json",
            "ctp",
            name="Catppuccin Frappe",
            family="Catppuccin",
        )
        self._init_with(themes_dir, active="ctp")
        groups = Theme.get_themes_grouped()
        family, entries = groups[0]
        assert entries[0].variant_name == "Catppuccin Frappe"

    def test_mixed_standalone_and_family_preserve_discovery_order(self, themes_dir: Path) -> None:
        # discovered alphabetically: catppuccin-a, dark, light, sakura
        _write_theme_file(
            themes_dir / "catppuccin-a.json",
            "catppuccin-a",
            family="Catppuccin",
            variant="A",
        )
        _write_theme_file(themes_dir / "dark.json", "dark", name="Dark")
        _write_theme_file(themes_dir / "light.json", "light", name="Light")
        _write_theme_file(themes_dir / "sakura.json", "sakura", name="Sakura", family="Sakura", variant="Sakura")
        self._init_with(themes_dir, active="light")
        groups = Theme.get_themes_grouped()
        ordered_families = [g[0] for g in groups]
        # Family appears at first variant's discovery position; standalone keep None.
        assert ordered_families == ["Catppuccin", None, None, "Sakura"]

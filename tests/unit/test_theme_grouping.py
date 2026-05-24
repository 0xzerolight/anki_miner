"""Tests for optional family/variant fields on theme JSON + grouping helper."""

from __future__ import annotations

from anki_miner.gui.resources.styles.theme import (
    REQUIRED_COLOR_KEYS,
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

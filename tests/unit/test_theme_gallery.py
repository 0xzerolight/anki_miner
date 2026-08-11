"""Theme-gallery discovery gates for malformed custom themes."""

import json
from pathlib import Path

from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme


def _theme(name: str) -> dict:
    return {
        "name": name,
        "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000"),
    }


def test_only_valid_custom_themes_are_selectable(tmp_path: Path):
    shipped_dir = tmp_path / "shipped"
    shipped_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()

    (shipped_dir / "light.json").write_text(json.dumps(_theme("Light")), encoding="utf-8")
    (user_dir / "custom.json").write_text(json.dumps(_theme("Custom")), encoding="utf-8")
    (user_dir / "deep.json").write_text("[" * 10_000 + "]" * 10_000, encoding="utf-8")
    invalid_color = _theme("Invalid Color")
    invalid_color["colors"]["primary"] = {}
    (user_dir / "invalid-color.json").write_text(json.dumps(invalid_color), encoding="utf-8")

    Theme.initialize(active="light", shipped_dir=shipped_dir, user_dir=user_dir)

    selectable = {entry.key for _family, entries in Theme.get_themes_grouped() for entry in entries}
    assert selectable == {"light", "custom"}

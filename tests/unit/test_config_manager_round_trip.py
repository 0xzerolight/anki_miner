"""Save→load JSON round-trip tests for GUIConfigManager.

Theme (with favorites), excluded_decks (Issue #38) and excluded_wordsets
(Issue #59) must survive a save→load cycle, with tuple fields coerced back to
tuples by ``AnkiMinerConfig.__post_init__``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch):
    """Redirect GUIConfigManager's CONFIG_FILE to a temp path for the test."""
    fake_config = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake_config)
    return fake_config


def test_config_version_does_not_shift_positional_fields():
    assert AnkiMinerConfig("Custom Deck").anki_deck_name == "Custom Deck"


class TestThemeFavoritesRoundTrip:
    def test_favorites_round_trip_through_json(self, isolated_config_file):
        base = create_default_config()
        from dataclasses import replace

        new = replace(base, theme="sakura", theme_favorites=("sakura", "tokyo-night"))
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        assert loaded.theme == "sakura"
        # The dataclass coerces list/tuple in __post_init__; we get a tuple back.
        assert loaded.theme_favorites == ("sakura", "tokyo-night")

    def test_themes_root_round_trip(self, isolated_config_file, tmp_path: Path):
        base = create_default_config()
        from dataclasses import replace

        custom_root = tmp_path / "custom-themes"
        new = replace(base, themes_root=custom_root)
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        assert loaded.themes_root == custom_root


class TestExcludedDecksRoundTrip:
    """excluded_decks (Issue #38) survives a save→load JSON round-trip as a tuple."""

    def test_excluded_decks_round_trip(self, isolated_config_file):
        base = create_default_config()
        from dataclasses import replace

        new = replace(base, excluded_decks=("Remembering The Kanji", "Kanji Writing"))
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        # JSON stores a list; __post_init__ coerces it back to a tuple.
        assert loaded.excluded_decks == ("Remembering The Kanji", "Kanji Writing")

    def test_default_is_empty_tuple(self, isolated_config_file):
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.excluded_decks == ()


class TestExcludedWordsetsRoundTrip:
    """excluded_wordsets (Issue #59) survives a save→load JSON round-trip as a tuple."""

    def test_excluded_wordsets_round_trip(self, isolated_config_file):
        base = create_default_config()
        from dataclasses import replace

        new = replace(base, excluded_wordsets=("surnames", "place-names"))
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        # JSON stores a list; __post_init__ coerces it back to a tuple.
        assert loaded.excluded_wordsets == ("surnames", "place-names")

    def test_default_round_trips_all_wordsets(self, isolated_config_file):
        """Default-ON (junk-reduction r3): the bundled sets survive save→load."""
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.excluded_wordsets == ("surnames", "given-names", "place-names", "org-product")

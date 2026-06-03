"""Tests for the QSettings → gui_config.json theme migration.

Pre-v2.5 the active theme key was stored in QSettings. v2.5 moves it into
gui_config.json. These tests cover both first-launch paths:

  * no gui_config.json at all (e.g. user only ever changed the theme)
  * gui_config.json exists but pre-dates the new ``theme`` field
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import create_default_config
from anki_miner.gui.utils import config_manager
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch):
    """Redirect GUIConfigManager's CONFIG_FILE to a temp path for the test."""
    fake_config = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake_config)
    return fake_config


class _FakeQSettings:
    """Drop-in QSettings stub used to control the legacy value at module level."""

    def __init__(self, *_args, **_kwargs):
        pass

    _store: dict[str, str] = {}

    @classmethod
    def set_value(cls, value: str | None) -> None:
        cls._store = {} if value is None else {"theme": value}

    def contains(self, key: str) -> bool:  # noqa: D401 — Qt API mirror
        return key in self._store

    def value(self, key: str, default=None):
        return self._store.get(key, default)


@pytest.fixture
def fake_qsettings(monkeypatch):
    """Patch the PyQt6.QtCore.QSettings symbol that the migration imports."""
    # The helper imports QSettings lazily inside the function. Patch the
    # PyQt6.QtCore module so the deferred import returns our fake.
    from PyQt6 import QtCore

    monkeypatch.setattr(QtCore, "QSettings", _FakeQSettings)
    _FakeQSettings.set_value(None)
    yield _FakeQSettings
    _FakeQSettings.set_value(None)


class TestQSettingsMigration:
    def test_no_config_file_no_qsettings_returns_default(self, isolated_config_file, fake_qsettings):
        # Fresh install: nothing on disk, nothing in QSettings.
        config = GUIConfigManager.load_config()
        assert config.theme == create_default_config().theme

    def test_no_config_file_with_qsettings_value_seeds_theme(self, isolated_config_file, fake_qsettings):
        # User upgraded from a pre-v2.5 build that only ever wrote the theme to
        # QSettings. The default config should adopt that theme.
        fake_qsettings.set_value("sakura")
        config = GUIConfigManager.load_config()
        assert config.theme == "sakura"

    def test_existing_config_without_theme_inherits_qsettings(self, isolated_config_file, fake_qsettings):
        # User on v2.4 customized other settings but theme stayed in QSettings.
        # Loading their config should migrate the theme over.
        isolated_config_file.write_text(json.dumps({"anki_deck_name": "Old Deck"}))
        fake_qsettings.set_value("dark")
        config = GUIConfigManager.load_config()
        assert config.theme == "dark"
        # And unrelated existing keys are preserved.
        assert config.anki_deck_name == "Old Deck"

    def test_existing_config_with_theme_wins_over_qsettings(self, isolated_config_file, fake_qsettings):
        # If both are present, the file is the source of truth (the migration
        # has already happened on a prior launch).
        isolated_config_file.write_text(json.dumps({"theme": "dark"}))
        fake_qsettings.set_value("sakura")
        config = GUIConfigManager.load_config()
        assert config.theme == "dark"


class TestThemeFavoritesRoundTrip:
    def test_favorites_round_trip_through_json(self, isolated_config_file, fake_qsettings):
        base = create_default_config()
        from dataclasses import replace

        new = replace(base, theme="sakura", theme_favorites=("sakura", "tokyo-night"))
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        assert loaded.theme == "sakura"
        # The dataclass coerces list/tuple in __post_init__; we get a tuple back.
        assert loaded.theme_favorites == ("sakura", "tokyo-night")

    def test_themes_root_round_trip(self, isolated_config_file, fake_qsettings, tmp_path: Path):
        base = create_default_config()
        from dataclasses import replace

        custom_root = tmp_path / "custom-themes"
        new = replace(base, themes_root=custom_root)
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        assert loaded.themes_root == custom_root


class TestExcludedDecksRoundTrip:
    """excluded_decks (Issue #38) survives a save→load JSON round-trip as a tuple."""

    def test_excluded_decks_round_trip(self, isolated_config_file, fake_qsettings):
        base = create_default_config()
        from dataclasses import replace

        new = replace(base, excluded_decks=("Remembering The Kanji", "Kanji Writing"))
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        # JSON stores a list; __post_init__ coerces it back to a tuple.
        assert loaded.excluded_decks == ("Remembering The Kanji", "Kanji Writing")

    def test_default_is_empty_tuple(self, isolated_config_file, fake_qsettings):
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.excluded_decks == ()


class TestExcludedWordsetsRoundTrip:
    """excluded_wordsets (Issue #59) survives a save→load JSON round-trip as a tuple."""

    def test_excluded_wordsets_round_trip(self, isolated_config_file, fake_qsettings):
        base = create_default_config()
        from dataclasses import replace

        new = replace(base, excluded_wordsets=("surnames", "place-names"))
        GUIConfigManager.save_config(new)

        loaded = GUIConfigManager.load_config()
        # JSON stores a list; __post_init__ coerces it back to a tuple.
        assert loaded.excluded_wordsets == ("surnames", "place-names")

    def test_default_is_empty_tuple(self, isolated_config_file, fake_qsettings):
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.excluded_wordsets == ()


# Silence the unused-import lint warning when config_manager isn't directly
# referenced (the test reaches into PyQt6.QtCore to patch QSettings).
_ = config_manager

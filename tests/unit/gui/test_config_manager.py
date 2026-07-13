"""Tests for GUIConfigManager persistence and migration."""

import json
from dataclasses import replace
from pathlib import Path

from anki_miner.config import create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager


class TestLoadConfigMigration:
    """Regression tests for silent migration of removed fields."""

    def test_ignores_unknown_keys_without_wiping_user_settings(self, tmp_path, monkeypatch):
        """Old JSON files with removed fields must not reset the user's real config."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "anki_deck_name": "My Deck",
                    "ankiconnect_url": "http://example:8765",
                    "min_word_length": 3,  # removed field
                    "some_future_dead_field": "garbage",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = GUIConfigManager.load_config()

        assert config.anki_deck_name == "My Deck"
        assert config.ankiconnect_url == "http://example:8765"
        assert not hasattr(config, "min_word_length")

    def test_stray_custom_card_css_key_is_dropped(self, tmp_path, monkeypatch):
        """A gui_config.json from a version that had Custom CSS loads without error."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "anki_deck_name": "My Deck",
                    "custom_card_css": ".yomitan-glossary { color: red; }",  # removed field
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = GUIConfigManager.load_config()

        assert config.anki_deck_name == "My Deck"
        assert not hasattr(config, "custom_card_css")


class TestAnkiTagsRoundTrip:
    """Persistence of the anki_tags field through save/load."""

    def test_save_and_load_preserves_anki_tags(self, tmp_path, monkeypatch):
        """A custom anki_tags value must survive a save/load cycle verbatim."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = replace(create_default_config(), anki_tags="foo bar")
        GUIConfigManager.save_config(config)

        loaded = GUIConfigManager.load_config()

        assert loaded.anki_tags == "foo bar"

    def test_legacy_config_without_anki_tags_uses_default(self, tmp_path, monkeypatch):
        """A pre-anki_tags JSON file must load and fall back to the dataclass default."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "anki_deck_name": "Legacy Deck",
                    "ankiconnect_url": "http://example:8765",
                    # Note: anki_tags key intentionally absent.
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        loaded = GUIConfigManager.load_config()

        assert loaded.anki_tags == "auto-mined"
        assert loaded.anki_deck_name == "Legacy Deck"


class TestDictsRootRoundTrip:
    """Persistence of the Issue #45 dicts_root field through save/load."""

    def test_save_and_load_preserves_dicts_root(self, tmp_path, monkeypatch):
        """A non-default dicts_root must survive save/load as a Path object."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        external = tmp_path / "external_ssd_dicts"
        external.mkdir()

        config = replace(create_default_config(), dicts_root=external)
        GUIConfigManager.save_config(config)

        loaded = GUIConfigManager.load_config()

        assert isinstance(loaded.dicts_root, Path)
        assert loaded.dicts_root == external

    def test_dicts_root_serialized_as_string(self, tmp_path, monkeypatch):
        """The on-disk JSON must store dicts_root as a string so other readers
        (e.g. external tools, manual edits) don't trip on a Path repr."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        external = tmp_path / "elsewhere"
        external.mkdir()
        GUIConfigManager.save_config(replace(create_default_config(), dicts_root=external))

        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert raw["dicts_root"] == str(external)

    def test_legacy_config_without_dicts_root_uses_default(self, tmp_path, monkeypatch):
        """A pre-Issue-#45 JSON file must load and fall back to the dataclass default."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps({"anki_deck_name": "Legacy Deck"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        loaded = GUIConfigManager.load_config()

        assert isinstance(loaded.dicts_root, Path)
        # Default is ANKI_MINER_HOME / "dicts"; just confirm it ends in "dicts".
        assert loaded.dicts_root.name == "dicts"


class TestAudioPacksRootRoundTrip:
    """Persistence of the audio_packs_root field through save/load."""

    def test_save_and_load_preserves_audio_packs_root(self, tmp_path, monkeypatch):
        """A non-default audio_packs_root must survive save/load as a Path object."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        external = tmp_path / "external_ssd_audio"
        external.mkdir()

        config = replace(create_default_config(), audio_packs_root=external)
        GUIConfigManager.save_config(config)

        loaded = GUIConfigManager.load_config()

        assert isinstance(loaded.audio_packs_root, Path)
        assert loaded.audio_packs_root == external

    def test_audio_packs_root_serialized_as_string(self, tmp_path, monkeypatch):
        """The on-disk JSON must store audio_packs_root as a string."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        external = tmp_path / "elsewhere_audio"
        external.mkdir()
        GUIConfigManager.save_config(replace(create_default_config(), audio_packs_root=external))

        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert raw["audio_packs_root"] == str(external)

    def test_legacy_config_without_audio_packs_root_uses_default(self, tmp_path, monkeypatch):
        """A JSON file missing audio_packs_root must fall back to the dataclass default."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps({"anki_deck_name": "Legacy Deck"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        loaded = GUIConfigManager.load_config()

        assert isinstance(loaded.audio_packs_root, Path)
        assert loaded.audio_packs_root.name == "audio_packs"


class TestUiFontScaleRoundTrip:
    """Persistence of the Issue #63 ui_font_scale field through save/load."""

    def test_save_and_load_preserves_non_default_value(self, tmp_path, monkeypatch):
        """A non-default ui_font_scale must survive a save/load cycle."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = replace(create_default_config(), ui_font_scale=1.5)
        GUIConfigManager.save_config(config)

        loaded = GUIConfigManager.load_config()

        assert loaded.ui_font_scale == 1.5

    def test_legacy_config_missing_key_loads_to_default(self, tmp_path, monkeypatch):
        """A JSON file without ui_font_scale must fall back to the dataclass default of 1.0."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps({"anki_deck_name": "Legacy Deck"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        loaded = GUIConfigManager.load_config()

        assert loaded.ui_font_scale == 1.0

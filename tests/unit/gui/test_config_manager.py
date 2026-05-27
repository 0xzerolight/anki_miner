"""Tests for GUIConfigManager persistence and migration."""

import json
from dataclasses import replace

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


class TestAllowedPosMigration:
    """Migration of pre-v2.3.2 allowed_pos defaults that lacked 代名詞."""

    def test_migrate_allowed_pos_replaces_old_default(self, tmp_path, monkeypatch):
        """A stored allowed_pos matching the legacy default must gain 代名詞."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "allowed_pos": ["名詞", "動詞", "形容詞", "副詞", "形状詞"],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = GUIConfigManager.load_config()

        assert "代名詞" in config.allowed_pos
        assert set(config.allowed_pos) == set(create_default_config().allowed_pos)

    def test_migrate_allowed_pos_replaces_old_default_regardless_of_order(self, tmp_path, monkeypatch):
        """Order of items in saved JSON must not block migration (set compare)."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    # Same items as legacy default but reordered
                    "allowed_pos": ["形状詞", "副詞", "形容詞", "動詞", "名詞"],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = GUIConfigManager.load_config()

        assert "代名詞" in config.allowed_pos

    def test_migrate_allowed_pos_preserves_user_edits(self, tmp_path, monkeypatch):
        """A user-edited allowed_pos (different shape) must be left alone."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "allowed_pos": ["名詞", "動詞"],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = GUIConfigManager.load_config()

        assert config.allowed_pos == ["名詞", "動詞"]

    def test_migrate_allowed_pos_already_has_pronouns(self, tmp_path, monkeypatch):
        """A list that already contains 代名詞 must not be rewritten."""
        existing = ["名詞", "動詞", "形容詞", "副詞", "形状詞", "代名詞"]
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps({"allowed_pos": existing}),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        config = GUIConfigManager.load_config()

        assert config.allowed_pos == existing


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


class TestCardStylingRoundTrip:
    """Persistence of the Issue #44 card-styling fields through save/load."""

    def test_save_and_load_preserves_card_styling(self, tmp_path, monkeypatch):
        """Custom CSS and the default-stylesheet toggle must survive save/load."""
        cfg_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        css = '.yomitan-glossary { color: red; }\n[data-sc-content|="example-sentence"] { display: none; }'
        config = replace(create_default_config(), use_default_card_stylesheet=False, custom_card_css=css)
        GUIConfigManager.save_config(config)

        loaded = GUIConfigManager.load_config()

        assert loaded.use_default_card_stylesheet is False
        assert loaded.custom_card_css == css

    def test_legacy_config_without_card_styling_uses_defaults(self, tmp_path, monkeypatch):
        """A pre-Issue-#44 JSON file must load and fall back to the dataclass defaults."""
        cfg_file = tmp_path / "gui_config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "anki_deck_name": "Legacy Deck",
                    # Note: card-styling keys intentionally absent.
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_file)

        loaded = GUIConfigManager.load_config()

        assert loaded.use_default_card_stylesheet is True
        assert loaded.custom_card_css == ""

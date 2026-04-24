"""Tests for GUIConfigManager persistence and migration."""

import json

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

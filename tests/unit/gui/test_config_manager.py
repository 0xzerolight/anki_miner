"""Tests for GUIConfigManager persistence and migration."""

import json

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

    def test_migrate_allowed_pos_replaces_old_default_regardless_of_order(
        self, tmp_path, monkeypatch
    ):
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

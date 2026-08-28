"""The mining-language config field and its duplicated-literal sync guard."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages import AVAILABLE_LANGUAGES


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


def test_language_defaults_to_ja():
    assert AnkiMinerConfig().language == "ja"


def test_config_literal_matches_available_languages():
    """config must not import the languages package, so the tuple is a
    hand-duplicated literal; this assertion keeps the two in sync."""
    assert _LANGUAGE_CODES == AVAILABLE_LANGUAGES


def test_unknown_language_resets_to_ja():
    assert AnkiMinerConfig(language="tlh").language == "ja"


def test_language_is_normalized():
    assert AnkiMinerConfig(language="  ZH ").language == "zh"


def test_language_survives_replace():
    assert dataclasses.replace(AnkiMinerConfig(), language="ko").language == "ko"


def test_language_round_trips_through_json(isolated_config_file):
    GUIConfigManager.save_config(dataclasses.replace(create_default_config(), language="zh"))
    assert GUIConfigManager.load_config().language == "zh"


def test_old_build_drops_the_key_without_raising(isolated_config_file):
    """Downgrade simulation: an unknown key is dropped by the valid-keys filter
    in _migrate_dict, exactly as `language` would be on a pre-Stage-0 build."""
    payload = {"language": "zh", "language_of_the_future": "xx"}
    migrated = GUIConfigManager._migrate_dict(payload)
    assert "language_of_the_future" not in migrated
    assert AnkiMinerConfig(**migrated).language == "zh"
    isolated_config_file.write_text(json.dumps({"language_of_the_future": "xx"}), encoding="utf-8")
    assert GUIConfigManager.load_config().language == "ja"

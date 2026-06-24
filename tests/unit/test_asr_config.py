"""Tests for ASR-related config fields: asr_model default/validation, asr_models_root derivation,
and round-trip persistence through GUIConfigManager."""

import json
from dataclasses import replace
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager

# ---------------------------------------------------------------------------
# asr_model field
# ---------------------------------------------------------------------------


def test_asr_model_default():
    """Default value must be 'large-v3'."""
    assert AnkiMinerConfig().asr_model == "large-v3"


def test_asr_model_valid_small():
    """'small' is an accepted value."""
    assert AnkiMinerConfig(asr_model="small").asr_model == "small"


def test_asr_model_invalid_resets_to_default():
    """Any value not in the known set must be reset to 'large-v3'."""
    assert AnkiMinerConfig(asr_model="medium").asr_model == "large-v3"
    assert AnkiMinerConfig(asr_model="").asr_model == "large-v3"
    assert AnkiMinerConfig(asr_model="LARGE-V3").asr_model == "large-v3"


# ---------------------------------------------------------------------------
# asr_models_root field
# ---------------------------------------------------------------------------


def test_asr_models_root_resolves_under_anki_miner_home():
    """asr_models_root must be derived from ANKI_MINER_HOME, defaulting to ANKI_MINER_HOME/asr_models."""
    from anki_miner.config.paths import ANKI_MINER_HOME

    cfg = AnkiMinerConfig()
    assert cfg.asr_models_root == ANKI_MINER_HOME / "asr_models"


def test_asr_models_root_is_path():
    """asr_models_root must be a Path instance."""
    assert isinstance(AnkiMinerConfig().asr_models_root, Path)


# ---------------------------------------------------------------------------
# Round-trip persistence (save → load via GUIConfigManager)
# ---------------------------------------------------------------------------


def test_asr_model_round_trips(monkeypatch):
    """asr_model persists through save/load without loss."""
    cfg = replace(create_default_config(), asr_model="small")
    GUIConfigManager.save_config(cfg)
    loaded = GUIConfigManager.load_config()
    assert loaded.asr_model == "small"


def test_old_config_without_asr_model_defaults_to_large_v3():
    """A legacy config JSON lacking asr_model must still load with the default."""
    cfg_path = GUIConfigManager.CONFIG_FILE
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    loaded = GUIConfigManager.load_config()
    assert loaded.asr_model == "large-v3"

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
# asr_device field
# ---------------------------------------------------------------------------


def test_asr_device_default_auto():
    """asr_device defaults to 'auto' (GPU if usable, else CPU)."""
    assert AnkiMinerConfig().asr_device == "auto"


def test_asr_device_valid_values_pass_through():
    """Valid asr_device values are preserved unchanged."""
    assert AnkiMinerConfig(asr_device="auto").asr_device == "auto"
    assert AnkiMinerConfig(asr_device="cuda").asr_device == "cuda"
    assert AnkiMinerConfig(asr_device="cpu").asr_device == "cpu"
    assert AnkiMinerConfig(asr_device="vulkan").asr_device == "vulkan"


def test_asr_device_vulkan_round_trips_via_replace():
    """asr_device='vulkan' is preserved (not reset) when built via replace()."""
    cfg = replace(create_default_config(), asr_device="vulkan")
    assert cfg.asr_device == "vulkan"


def test_asr_device_invalid_resets_to_auto():
    """An unknown asr_device value resets to 'auto'."""
    assert AnkiMinerConfig(asr_device="gpu").asr_device == "auto"
    assert AnkiMinerConfig(asr_device="metal").asr_device == "auto"
    assert AnkiMinerConfig(asr_device="rocm").asr_device == "auto"
    assert AnkiMinerConfig(asr_device="").asr_device == "auto"


# ---------------------------------------------------------------------------
# cuda_libs_root field
# ---------------------------------------------------------------------------


def test_cuda_libs_root_resolves_under_anki_miner_home():
    """cuda_libs_root must be derived from ANKI_MINER_HOME, defaulting to ANKI_MINER_HOME/cuda_libs."""
    from anki_miner.config.paths import ANKI_MINER_HOME

    cfg = AnkiMinerConfig()
    assert cfg.cuda_libs_root == ANKI_MINER_HOME / "cuda_libs"


def test_cuda_libs_root_is_path():
    """cuda_libs_root must be a Path instance."""
    assert isinstance(AnkiMinerConfig().cuda_libs_root, Path)


# ---------------------------------------------------------------------------
# onnx_pack_root field
# ---------------------------------------------------------------------------


def test_onnx_pack_root_resolves_under_anki_miner_home():
    """onnx_pack_root must be derived from ANKI_MINER_HOME, defaulting to ANKI_MINER_HOME/onnx_pack."""
    from anki_miner.config.paths import ANKI_MINER_HOME

    cfg = AnkiMinerConfig()
    assert cfg.onnx_pack_root == ANKI_MINER_HOME / "onnx_pack"


def test_onnx_pack_root_is_path():
    """onnx_pack_root must be a Path instance."""
    assert isinstance(AnkiMinerConfig().onnx_pack_root, Path)


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


# ---------------------------------------------------------------------------
# Drift guard: config validation duplicates the model set (kept import-free)
# and must stay in sync with model_manager's authoritative KNOWN_MODELS.
# Importing model_manager is light (no faster_whisper/numpy at module level),
# so this runs in the default job, not just test-asr.
# ---------------------------------------------------------------------------


def test_config_accepts_every_known_model():
    """Every model_manager.KNOWN_MODELS entry must survive config validation.

    If the manager adds a model the config's hardcoded set forgot, this catches
    it — otherwise a hand-edited config requesting the new model would silently
    reset to the default.
    """
    from anki_miner.services.asr import model_manager

    for name in model_manager.KNOWN_MODELS:
        cfg = replace(create_default_config(), asr_model=name)
        assert cfg.asr_model == name


def test_config_default_matches_manager_default():
    from anki_miner.services.asr import model_manager

    assert create_default_config().asr_model == model_manager.DEFAULT_MODEL

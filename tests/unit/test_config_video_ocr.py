"""The two Video OCR config fields: region validation and the models root."""

from __future__ import annotations

import dataclasses
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


def test_defaults():
    # Imported here, not at module top: the home isolation patches this binding
    # after collection, and the default factory reads the patched one.
    from anki_miner.config.paths import ANKI_MINER_HOME

    config = AnkiMinerConfig()
    assert config.video_ocr_region == ()
    assert config.video_ocr_models_root == ANKI_MINER_HOME / "ocr_models"


def test_a_valid_region_is_rounded_and_stored_as_a_tuple():
    config = AnkiMinerConfig(video_ocr_region=[0.123456, 0.8, 0.75, 0.15])  # type: ignore[arg-type]
    assert config.video_ocr_region == (0.1235, 0.8, 0.75, 0.15)


@pytest.mark.parametrize(
    "bad",
    [(0.1, 0.8, 0.8), (0.1, 0.8, 0.0, 0.1), (-0.1, 0.8, 0.5, 0.1), (0.5, 0.5, 0.6, 0.1), ("a", "b", "c", "d"), "x"],
)
def test_an_invalid_region_resets_to_unset(bad):
    assert AnkiMinerConfig(video_ocr_region=bad).video_ocr_region == ()  # type: ignore[arg-type]


def test_the_region_round_trips_through_json(isolated_config_file):
    region = (0.1235, 0.8, 0.75, 0.15)
    GUIConfigManager.save_config(dataclasses.replace(create_default_config(), video_ocr_region=region))

    loaded = GUIConfigManager.load_config()
    # JSON stores a list; __post_init__ validates it back into a tuple.
    assert loaded.video_ocr_region == region
    assert type(loaded.video_ocr_region) is tuple

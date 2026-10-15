"""The six settings the 2026-10-15 feature batch adds (master plan "Fixed names").

Four are on for a fresh install and off for a config saved before the batch
(GUIConfigManager's load-only keep_legacy_feature_switches); two are off for
everyone. An import that lacks a key keeps the current value.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS

#: On for a fresh install, off for a config file written before the batch.
FRESH_INSTALL_ON = ("normalize_audio_loudness", "crop_manga_to_text", "pick_best_sentence", "skip_opening_ending")
#: Off for everyone.
ALWAYS_OFF = ("known_from_reviewed_cards", "sync_anki_after_run")
ALL_SIX = FRESH_INSTALL_ON + ALWAYS_OFF


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch) -> Path:
    cfg_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_path)
    return cfg_path


def test_a_fresh_config_has_the_owner_chosen_defaults():
    config = AnkiMinerConfig()

    assert {name: getattr(config, name) for name in FRESH_INSTALL_ON} == dict.fromkeys(FRESH_INSTALL_ON, True)
    assert {name: getattr(config, name) for name in ALWAYS_OFF} == dict.fromkeys(ALWAYS_OFF, False)


@pytest.mark.parametrize("name", ALL_SIX)
def test_each_switch_is_global_and_portable(name):
    """One preference for every mining language, and it travels in an export."""
    assert name not in LANGUAGE_SCOPED_FIELDS
    assert name not in GUIConfigManager.machine_specific_fields()


@pytest.mark.parametrize("name", ALL_SIX)
def test_each_switch_survives_a_save_load_round_trip(tmp_config: Path, name):
    flipped = replace(create_default_config(), **{name: not getattr(create_default_config(), name)})
    GUIConfigManager.save_config(flipped)

    saved = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert saved[name] is getattr(flipped, name)
    assert getattr(GUIConfigManager.load_config(), name) is getattr(flipped, name)

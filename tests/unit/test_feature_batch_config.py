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


def _saved_before_the_batch() -> dict:
    """A schema-4 gui_config.json as the last release wrote it: every field but the six."""
    payload = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(create_default_config()))
    for name in ALL_SIX:
        payload.pop(name)
    payload["config_schema_version"] = 4
    return payload


def test_a_config_saved_before_the_batch_keeps_new_features_off(tmp_config: Path):
    """Review Focus 1: an existing user upgrades; nothing they mine changes until they opt in."""
    tmp_config.write_text(json.dumps(_saved_before_the_batch()), encoding="utf-8")

    loaded = GUIConfigManager.load_config()

    assert {name: getattr(loaded, name) for name in ALL_SIX} == dict.fromkeys(ALL_SIX, False)
    # The rule touches only those four fields: everything else is the saved value.
    assert replace(loaded, **dict.fromkeys(FRESH_INSTALL_ON, True)) == create_default_config()


def test_a_fresh_install_gets_the_defaults(tmp_config: Path):
    assert not tmp_config.exists()

    loaded = GUIConfigManager.load_config()

    assert {name: getattr(loaded, name) for name in FRESH_INSTALL_ON} == dict.fromkeys(FRESH_INSTALL_ON, True)
    assert {name: getattr(loaded, name) for name in ALWAYS_OFF} == dict.fromkeys(ALWAYS_OFF, False)


def test_a_saved_fresh_install_keeps_its_switches_on(tmp_config: Path):
    """After its first save a new install carries the keys, so the legacy rule never fires on it."""
    GUIConfigManager.save_config(create_default_config())

    loaded = GUIConfigManager.load_config()

    assert {name: getattr(loaded, name) for name in FRESH_INSTALL_ON} == dict.fromkeys(FRESH_INSTALL_ON, True)


def test_an_explicit_value_in_a_saved_file_wins(tmp_config: Path):
    payload = _saved_before_the_batch()
    payload["pick_best_sentence"] = True
    payload["skip_opening_ending"] = False
    tmp_config.write_text(json.dumps(payload), encoding="utf-8")

    loaded = GUIConfigManager.load_config()

    assert loaded.pick_best_sentence is True
    assert loaded.skip_opening_ending is False
    assert loaded.normalize_audio_loudness is False
    assert loaded.crop_manga_to_text is False


def test_a_markerless_old_config_keeps_new_features_off(tmp_config: Path):
    """A file from before schema versioning (no marker at all) is an existing install too."""
    tmp_config.write_text(json.dumps({"anki_deck_name": "Existing"}), encoding="utf-8")

    loaded = GUIConfigManager.load_config()

    assert loaded.anki_deck_name == "Existing"
    assert {name: getattr(loaded, name) for name in FRESH_INSTALL_ON} == dict.fromkeys(FRESH_INSTALL_ON, False)


def test_a_settings_profile_saved_before_the_batch_keeps_new_features_off(tmp_config: Path):
    """Profiles load through the same _parse_and_migrate (ProfileStore.read_profile), as D18 relies on."""
    from anki_miner.gui.utils.profile_store import ProfileStore

    directory = ProfileStore.profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "anime.json").write_text(json.dumps(_saved_before_the_batch()), encoding="utf-8")

    profile = ProfileStore.read_profile("anime")

    assert {name: getattr(profile, name) for name in ALL_SIX} == dict.fromkeys(ALL_SIX, False)


def test_the_legacy_rule_is_named_in_the_migration_receipt(tmp_config: Path, caplog):
    tmp_config.write_text(json.dumps(_saved_before_the_batch()), encoding="utf-8")

    with caplog.at_level("INFO", logger="anki_miner.gui.utils.config_manager"):
        GUIConfigManager.load_config()

    receipt = "\n".join(r.getMessage() for r in caplog.records if "Config migrated" in r.getMessage())
    for name in FRESH_INSTALL_ON:
        assert f"legacy_off_{name}" in receipt

"""config.key_bindings: overrides only, read-only, and forgiving of a hand edit."""

from __future__ import annotations

import json
import types
from dataclasses import replace

import pytest
from PyQt6.QtGui import QKeySequence

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.key_bindings import resolve_bindings
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS

PORTABLE = QKeySequence.SequenceFormat.PortableText


def test_the_default_is_no_overrides_and_read_only(test_config: AnkiMinerConfig) -> None:
    assert test_config.key_bindings == {}
    assert isinstance(test_config.key_bindings, types.MappingProxyType)


def test_replace_wraps_a_plain_dict(test_config: AnkiMinerConfig) -> None:
    config = replace(test_config, key_bindings={"curator.mark_known": "J"})
    assert isinstance(config.key_bindings, types.MappingProxyType)
    with pytest.raises(TypeError):
        config.key_bindings["curator.mark_known"] = "K"  # type: ignore[index]


def test_global_and_portable() -> None:
    assert "key_bindings" not in LANGUAGE_SCOPED_FIELDS
    assert "key_bindings" not in GUIConfigManager.machine_specific_fields()


def test_overrides_survive_a_save_and_a_load(test_config: AnkiMinerConfig) -> None:
    overrides = {"curator.mark_known": "J", "curator.play_pause": "", "app.open_settings": "Ctrl+Shift+S"}
    GUIConfigManager.save_config(replace(test_config, key_bindings=overrides))
    assert dict(GUIConfigManager.load_config().key_bindings) == overrides


def _load_hand_edited(key_bindings: object) -> AnkiMinerConfig:
    GUIConfigManager.CONFIG_FILE.write_text(json.dumps({"key_bindings": key_bindings}), encoding="utf-8")
    return GUIConfigManager.load_config()


def test_hand_edited_entries_fall_back_one_action_at_a_time(qapp) -> None:
    """Review Focus 3: each bad entry costs only its own action, and nothing raises."""
    config = _load_hand_edited(
        {
            "curator.mark_known": "Ctrl+Nonsense+Key",  # unreadable -> its default, D
            "curator.no_such_action": "X",  # unknown id -> ignored
            "curator.toggle_include": 5,  # not text -> dropped on load
            "curator.play_pause": "P",  # readable -> kept
        }
    )
    assert "curator.toggle_include" not in config.key_bindings
    assert config.key_bindings["curator.play_pause"] == "P"
    keys = resolve_bindings(config.key_bindings)
    assert keys["curator.mark_known"].toString(PORTABLE) == "D"
    assert keys["curator.toggle_include"].toString(PORTABLE) == "S"
    assert keys["curator.play_pause"].toString(PORTABLE) == "P"
    assert "curator.no_such_action" not in keys


def test_a_non_mapping_value_loads_as_no_overrides() -> None:
    assert _load_hand_edited(["S", "D"]).key_bindings == {}

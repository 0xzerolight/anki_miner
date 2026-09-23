"""``hidden_utilities``: the Utilities tools the tab leaves out (hidden keys only)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS


def test_the_default_hides_nothing():
    assert AnkiMinerConfig().hidden_utilities == ()


def test_a_json_list_becomes_a_tuple():
    assert AnkiMinerConfig(hidden_utilities=["retime", "mokuro"]).hidden_utilities == ("retime", "mokuro")


def test_it_survives_a_save_load_round_trip():
    GUIConfigManager.save_config(replace(AnkiMinerConfig(), hidden_utilities=("retime", "booksync")))

    assert GUIConfigManager.load_config().hidden_utilities == ("retime", "booksync")


@pytest.mark.parametrize("garbage", ["retime", [1, "retime"], {"retime": True}])
def test_a_hand_edited_garbage_value_loads_as_nothing_hidden(garbage):
    GUIConfigManager.save_config(AnkiMinerConfig())
    path = GUIConfigManager.CONFIG_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hidden_utilities"] = garbage
    path.write_text(json.dumps(data), encoding="utf-8")

    assert GUIConfigManager.load_config().hidden_utilities == ()


def test_it_is_global_and_portable():
    assert "hidden_utilities" not in LANGUAGE_SCOPED_FIELDS
    assert "hidden_utilities" not in GUIConfigManager.machine_specific_fields()

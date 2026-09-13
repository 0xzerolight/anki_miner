"""D7: a chain kind this build does not know is dropped on load, and the rest loads.

The contract §4.11 designs for (an older build drops kinds it does not know and
keeps the rest). Synthetic kinds, so Stage W (which adds wiktionary/edgetts with
their fetcher and labels) never has to edit this test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AudioSourceEntry, ChainEntry, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


def test_unknown_kinds_are_dropped_and_the_rest_loads(isolated_config_file):
    payload = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(create_default_config()))
    payload["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
    payload["expression_audio_chain"] = [
        {"kind": "zz_future_kind", "pack_id": None, "url": None, "enabled": True},
        {"kind": "zz_future_audio_kind", "pack_id": None, "url": None, "enabled": True},
        {"kind": "googletts", "pack_id": None, "url": None, "enabled": True},
    ]
    payload["dictionary_chain"] = [
        {"kind": "zz_future_kind", "dict_id": None, "enabled": False},
        {"kind": "jisho", "dict_id": None, "enabled": True},
    ]
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")

    loaded = GUIConfigManager.load_config()

    assert loaded.expression_audio_chain == (AudioSourceEntry(kind="googletts"),)
    assert loaded.dictionary_chain == (ChainEntry(kind="jisho"),)

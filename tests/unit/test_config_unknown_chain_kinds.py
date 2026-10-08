"""D7: chain kinds this build does not know are dropped on load, and the rest loads.

The contract §4.11 designs for (an older build drops kinds it does not know and
keeps the rest). Synthetic kinds, so Stage W (which adds wiktionary/edgetts with
their fetcher and labels) never has to edit this test.

Jisho, the online dictionary removed from the app, is one such kind: a saved
entry drops wherever a config is read (gui_config.json, a profile, a parked
language) and the Config migrated receipt names it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from anki_miner.config import AudioSourceEntry, ChainEntry, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


_CONFIG_LOGGER = "anki_miner.gui.utils.config_manager"
_JISHO = {"kind": "jisho", "dict_id": None, "enabled": True}


def _current_payload() -> dict:
    payload = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(create_default_config()))
    payload["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
    return payload


def _receipt(caplog) -> str:
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Config migrated:")]
    return line


def test_unknown_kinds_are_dropped_and_the_rest_loads(isolated_config_file):
    payload = _current_payload()
    payload["expression_audio_chain"] = [
        {"kind": "zz_future_kind", "pack_id": None, "url": None, "enabled": True},
        {"kind": "zz_future_audio_kind", "pack_id": None, "url": None, "enabled": True},
        {"kind": "googletts", "pack_id": None, "url": None, "enabled": True},
    ]
    payload["dictionary_chain"] = [
        {"kind": "zz_future_kind", "dict_id": None, "enabled": False},
        {"kind": "indexed", "dict_id": "keep", "enabled": True},
    ]
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")

    loaded = GUIConfigManager.load_config()

    assert loaded.expression_audio_chain == (AudioSourceEntry(kind="googletts"),)
    assert loaded.dictionary_chain == (ChainEntry(kind="indexed", dict_id="keep"),)


def test_jisho_entry_is_dropped_and_named_in_the_receipt(isolated_config_file, caplog):
    payload = _current_payload()
    payload["dictionary_chain"] = [
        {"kind": "indexed", "dict_id": "jitendex", "enabled": True},
        _JISHO,
        {"kind": "indexed", "dict_id": "jmdict-english", "enabled": False},
    ]
    payload["jisho_api_url"] = "https://jisho.org/api/v1/search/words"
    payload["jisho_delay"] = 0.5
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
        loaded = GUIConfigManager.load_config()

    assert loaded.dictionary_chain == (
        ChainEntry(kind="indexed", dict_id="jitendex"),
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
    )
    line = _receipt(caplog)
    assert "drop_jisho" in line
    assert "jisho_api_url" in line and "jisho_delay" in line


def test_jisho_only_chain_loads_empty(isolated_config_file):
    payload = _current_payload()
    payload["dictionary_chain"] = [_JISHO]
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")

    # Empty, not the defaults: the user had no dictionary, and the Dictionaries
    # page's empty state is the honest answer.
    assert GUIConfigManager.load_config().dictionary_chain == ()


def test_jisho_in_a_parked_language_is_dropped(isolated_config_file, caplog):
    payload = _current_payload()
    payload["language"] = "ko"
    payload["dictionary_chain"] = [{"kind": "indexed", "dict_id": "krdict", "enabled": True}]
    payload["language_stash"] = {
        "ja": {
            "dictionary_chain": [
                {"kind": "jisho", "dict_id": None, "enabled": False},
                {"kind": "indexed", "dict_id": "jitendex", "enabled": True},
                {"kind": "indexed", "dict_id": "jmdict-english", "enabled": True},
            ]
        }
    }
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
        loaded = GUIConfigManager.load_config()

    assert loaded.dictionary_chain == (ChainEntry(kind="indexed", dict_id="krdict"),)
    assert loaded.language_stash["ja"]["dictionary_chain"] == (
        ChainEntry(kind="indexed", dict_id="jitendex"),
        ChainEntry(kind="indexed", dict_id="jmdict-english"),
    )
    assert "drop_jisho" in _receipt(caplog)


def test_profile_with_jisho_reads_without_it(isolated_config_file):
    payload = _current_payload()
    payload["profile_name"] = "Old"
    payload["dictionary_chain"] = [
        {"kind": "indexed", "dict_id": "jitendex", "enabled": True},
        _JISHO,
        {"kind": "indexed", "dict_id": "jmdict-english", "enabled": True},
    ]
    path = ProfileStore._path_for("old")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert ProfileStore.read_profile("old").dictionary_chain == (
        ChainEntry(kind="indexed", dict_id="jitendex"),
        ChainEntry(kind="indexed", dict_id="jmdict-english"),
    )


def test_old_export_with_jisho_keys_imports_cleanly(tmp_path: Path):
    # Regression pin (passes before and after): the removed fields drop as
    # unknown keys, so the import dialog never lists them as invalid.
    source = tmp_path / "old-export.json"
    source.write_text(
        json.dumps(
            {"anki_deck_name": "Deck", "jisho_api_url": "https://jisho.org/api/v1/search/words", "jisho_delay": 0.5}
        ),
        encoding="utf-8",
    )

    result = GUIConfigManager.import_config(source, create_default_config())

    assert result.invalid_fields == []
    assert result.config.anki_deck_name == "Deck"

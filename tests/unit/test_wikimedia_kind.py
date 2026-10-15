"""F-16: the ``wikimedia`` audio kind loads and saves, and a profile names its Commons prefixes."""

from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest

from anki_miner.config import AudioSourceEntry, create_default_config
from anki_miner.gui.utils import config_manager as config_manager_module
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages.profile import AudioDefaults

#: The allowlist v3.9.0 shipped: what an older build runs on load.
_V3_9_0_AUDIO_SOURCE_KINDS = ("pack", "jpod101", "googletts", "custom", "custom_json", "edgetts")

_CHAIN = [
    {"kind": "wikimedia", "pack_id": None, "url": None, "enabled": True},
    {"kind": "googletts", "pack_id": None, "url": None, "enabled": True},
]


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


def _write(path: Path, **overrides: object) -> None:
    payload = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(create_default_config()))
    payload["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_the_kind_is_in_the_literal_and_the_load_allowlist():
    literal = typing.get_args(typing.get_type_hints(AudioSourceEntry)["kind"])

    assert "wikimedia" in literal
    assert "wikimedia" in config_manager_module._AUDIO_SOURCE_KINDS


def test_a_wikimedia_row_round_trips(isolated_config_file):
    _write(isolated_config_file, expression_audio_chain=_CHAIN)

    loaded = GUIConfigManager.load_config()

    assert loaded.expression_audio_chain == (AudioSourceEntry(kind="wikimedia"), AudioSourceEntry(kind="googletts"))


def test_an_older_build_drops_the_row_and_keeps_google(isolated_config_file, monkeypatch):
    _write(isolated_config_file, expression_audio_chain=_CHAIN)
    monkeypatch.setattr(config_manager_module, "_AUDIO_SOURCE_KINDS", _V3_9_0_AUDIO_SOURCE_KINDS)

    assert GUIConfigManager.load_config().expression_audio_chain == (AudioSourceEntry(kind="googletts"),)


def test_a_profile_names_no_commons_prefixes_unless_it_says_so():
    audio = AudioDefaults(
        gtts_lang="xx", cache_stem_prefix="g", sentence_cache_stem_prefix="s", custom_fetcher_language="xx"
    )

    assert audio.wikimedia_prefixes == ()

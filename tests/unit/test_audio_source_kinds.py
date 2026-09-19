"""The edgetts audio kind round-trips through persistence, and an older build still loads it.

The load allowlist (``config_manager._AUDIO_SOURCE_KINDS``) serves the
top-level ``expression_audio_chain``, every chain parked in ``language_stash``
and settings profiles. The downgrade tests run the load path with the
allowlist v3.4.0 shipped, which is exactly what an older build executes.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AudioSourceEntry, create_default_config
from anki_miner.gui.utils import config_manager as config_manager_module
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile

#: The allowlist v3.4.0 shipped (config_manager.py, _migrate_expression_audio_chain).
_V3_4_0_AUDIO_SOURCE_KINDS = ("pack", "jpod101", "googletts", "custom", "custom_json")

_EDGE = {"kind": "edgetts", "pack_id": None, "url": None, "enabled": True}


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


def test_the_allowlist_is_the_literal():
    literal = typing.get_args(typing.get_type_hints(AudioSourceEntry)["kind"])
    assert set(config_manager_module._AUDIO_SOURCE_KINDS) == set(literal)
    assert "edgetts" in literal


def test_an_edgetts_entry_round_trips(isolated_config_file):
    chain = [
        {"kind": "googletts", "pack_id": None, "url": None, "enabled": True},
        _EDGE,
        {"kind": "jpod101", "pack_id": None, "url": None, "enabled": False},
    ]
    _write(isolated_config_file, expression_audio_chain=chain)

    loaded = GUIConfigManager.load_config()

    assert loaded.expression_audio_chain == (
        AudioSourceEntry(kind="googletts"),
        AudioSourceEntry(kind="edgetts"),
        AudioSourceEntry(kind="jpod101", enabled=False),
    )


def test_an_edgetts_entry_parked_in_the_stash_survives(isolated_config_file):
    _write(isolated_config_file, language_stash={"ko": {"expression_audio_chain": [_EDGE]}})

    loaded = GUIConfigManager.load_config()

    assert tuple(loaded.language_stash["ko"]["expression_audio_chain"]) == (AudioSourceEntry(kind="edgetts"),)


def test_an_edge_only_chain_gains_no_google_row(isolated_config_file):
    # fa/sl default to (edgetts,) because they have no Google voice; the
    # append-if-missing googletts row would be a dead row for them.
    _write(isolated_config_file, expression_audio_chain=[_EDGE])

    assert GUIConfigManager.load_config().expression_audio_chain == (AudioSourceEntry(kind="edgetts"),)


@pytest.mark.parametrize("code", AVAILABLE_LANGUAGES)
def test_every_default_chain_round_trips_unchanged(code):
    chain = get_profile(code).audio.default_chain
    data = {"expression_audio_chain": [dataclasses.asdict(entry) for entry in chain]}

    assert GUIConfigManager._migrate_expression_audio_chain(data)["expression_audio_chain"] == chain


def test_an_older_build_turns_an_edge_only_chain_into_its_own_default(isolated_config_file, monkeypatch):
    _write(isolated_config_file, expression_audio_chain=[_EDGE])
    monkeypatch.setattr(config_manager_module, "_AUDIO_SOURCE_KINDS", _V3_4_0_AUDIO_SOURCE_KINDS)

    assert GUIConfigManager.load_config().expression_audio_chain == (AudioSourceEntry(kind="googletts", enabled=False),)


def test_an_older_build_drops_edgetts_and_keeps_the_rest(isolated_config_file, monkeypatch):
    saved = replace(
        create_default_config(),
        expression_audio_chain=(
            AudioSourceEntry(kind="googletts"),
            AudioSourceEntry(kind="edgetts"),
            AudioSourceEntry(kind="jpod101", enabled=False),
        ),
    )
    payload = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(saved))
    payload["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
    isolated_config_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(config_manager_module, "_AUDIO_SOURCE_KINDS", _V3_4_0_AUDIO_SOURCE_KINDS)

    loaded = GUIConfigManager.load_config()

    assert loaded.expression_audio_chain == (
        AudioSourceEntry(kind="googletts"),
        AudioSourceEntry(kind="jpod101", enabled=False),
    )

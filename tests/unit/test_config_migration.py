"""Tests for dictionary_chain / expression_audio_chain persistence and legacy migration."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, ChainEntry
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_path)
    return cfg_path


def test_save_then_load_preserves_chain(tmp_config: Path):
    chain = (
        ChainEntry(kind="indexed", dict_id="custom-dict", enabled=True),
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
        ChainEntry(kind="jisho", dict_id=None, enabled=True),
    )
    config = AnkiMinerConfig()
    config = replace(config, dictionary_chain=chain)
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.dictionary_chain == chain


def test_legacy_use_offline_dict_stripped_and_default_chain_used(tmp_config: Path):
    """An old gui_config.json with use_offline_dict but no dictionary_chain
    should fall through to the dataclass default chain. The obsolete
    use_offline_dict key must be stripped so AnkiMinerConfig() doesn't see it.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "use_offline_dict": True,
                "jmdict_path": str(Path.home() / ".anki_miner" / "JMdict_e"),
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.dictionary_chain == (
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
        ChainEntry(kind="jisho", dict_id=None, enabled=False),
    )


def test_old_config_backfills_expression_audio_in_anki_fields(tmp_config: Path):
    """A saved config whose anki_fields lacks 'expression_audio' must get the
    default value merged in on load — not crash or silently omit the key.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_fields": {
                    "word": "Expression",
                    "sentence": "Sentence",
                    "definition": "MainDefinition",
                    "glossary": "",
                    "picture": "Picture",
                    "audio": "SentenceAudio",
                    "expression_furigana": "ExpressionFurigana",
                    "expression_reading": "",
                    "sentence_furigana": "SentenceFurigana",
                    "sentence_reading": "",
                    "pitch_position": "",
                    "pitch_category": "",
                    "frequency": "Frequency",  # user-set non-empty value
                    "source": "",
                    # expression_audio intentionally absent (old config)
                },
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    # New key must be present with the default empty string
    assert "expression_audio" in loaded.anki_fields
    assert loaded.anki_fields["expression_audio"] == ""
    # Existing user value must be preserved
    assert loaded.anki_fields["frequency"] == "Frequency"


def test_old_config_without_expression_audio_fields_gets_defaults(tmp_config: Path):
    """A saved config missing expression_audio_delay must fall through to
    dataclass defaults rather than crashing.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_deck_name": "MyDeck",
                # expression_audio_delay absent
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.anki_deck_name == "MyDeck"
    assert loaded.expression_audio_delay == 0.2


def test_obsolete_expression_audio_enabled_key_is_dropped(tmp_config: Path):
    """A legacy config carrying the removed expression_audio_enabled flag must
    load cleanly (the unknown key is dropped, not fatal), and a non-empty
    expression_audio field name alone activates the feature.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_deck_name": "MyDeck",
                "expression_audio_enabled": True,  # removed field — must be ignored
                "anki_fields": {"expression_audio": "ExpressionAudio"},
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.anki_deck_name == "MyDeck"
    assert not hasattr(loaded, "expression_audio_enabled")
    assert loaded.anki_fields["expression_audio"] == "ExpressionAudio"


def test_null_anki_fields_does_not_raise(tmp_config: Path):
    """Config JSON with anki_fields: null must not crash load_config; the
    corrupt value is replaced with full defaults while every OTHER saved
    setting survives (no whole-config reset).
    """
    tmp_config.write_text(json.dumps({"anki_fields": None, "anki_deck_name": "KeepMe", "expression_audio_delay": 1.5}))

    loaded = GUIConfigManager.load_config()
    assert "expression_audio" in loaded.anki_fields
    assert loaded.anki_deck_name == "KeepMe"
    assert loaded.expression_audio_delay == 1.5


def test_string_anki_fields_does_not_raise(tmp_config: Path):
    """Config JSON with anki_fields as a string must not crash load_config;
    same non-dict guard path as the null case.
    """
    tmp_config.write_text(json.dumps({"anki_fields": "legacy_string"}))

    loaded = GUIConfigManager.load_config()
    assert "expression_audio" in loaded.anki_fields


def test_legacy_use_offline_dict_false_is_stripped(tmp_config: Path):
    """Legacy use_offline_dict=False is silently dropped; default chain is used.

    Users that disabled the JMdict provider via the pre-chain UI will re-enable
    it through the new chain UI; the legacy false flag is not propagated.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "use_offline_dict": False,
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    # Default chain has jmdict-english enabled; the legacy false flag is dropped.
    assert loaded.dictionary_chain == (
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
        ChainEntry(kind="jisho", dict_id=None, enabled=False),
    )


# ---------------------------------------------------------------------------
# expression_audio_chain migration tests
# ---------------------------------------------------------------------------


def test_save_then_load_preserves_audio_chain(tmp_config: Path):
    """A config with pack + jpod101 entries survives save/load unchanged.

    A persisted chain that already contains a googletts entry is not
    appended to again.
    """
    chain = (
        AudioSourceEntry(kind="pack", pack_id="nhk16", enabled=True),
        AudioSourceEntry(kind="pack", pack_id="forvo-jp", enabled=False),
        AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        AudioSourceEntry(kind="googletts", pack_id=None, enabled=False),
    )
    config = replace(AnkiMinerConfig(), expression_audio_chain=chain)
    GUIConfigManager.save_config(config)

    loaded = GUIConfigManager.load_config()
    assert loaded.expression_audio_chain == chain


def test_absent_audio_chain_yields_default(tmp_config: Path):
    """An old gui_config.json without expression_audio_chain uses the default."""
    tmp_config.write_text(json.dumps({"anki_deck_name": "MyDeck"}))

    loaded = GUIConfigManager.load_config()
    assert loaded.expression_audio_chain == (
        AudioSourceEntry(kind="jpod101"),
        AudioSourceEntry(kind="googletts", enabled=False),
    )


def test_migrate_expression_audio_chain_rebuilds_from_dicts():
    """_migrate_expression_audio_chain converts list[dict] to tuple[AudioSourceEntry]."""
    data = {
        "expression_audio_chain": [
            {"kind": "pack", "pack_id": "nhk16", "enabled": True},
            {"kind": "jpod101", "pack_id": None, "enabled": False},
            {"kind": "googletts", "pack_id": None, "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert isinstance(chain, tuple)
    assert chain == (
        AudioSourceEntry(kind="pack", pack_id="nhk16", enabled=True),
        AudioSourceEntry(kind="jpod101", pack_id=None, enabled=False),
        AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),
    )


def test_migrate_expression_audio_chain_googletts_round_trip():
    """A googletts dict rebuilds into an AudioSourceEntry preserving enabled."""
    data = {
        "expression_audio_chain": [
            {"kind": "googletts", "pack_id": None, "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert chain[0] == AudioSourceEntry(kind="googletts", pack_id=None, enabled=True)


def test_migrate_expression_audio_chain_appends_missing_googletts():
    """A persisted jpod101-only chain gains a disabled googletts entry."""
    data = {
        "expression_audio_chain": [
            {"kind": "jpod101", "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert len(chain) == 2
    assert chain[-1] == AudioSourceEntry(kind="googletts", pack_id=None, enabled=False)


def test_migrate_expression_audio_chain_no_duplicate_googletts():
    """A chain already containing googletts is not appended to."""
    data = {
        "expression_audio_chain": [
            {"kind": "jpod101", "enabled": True},
            {"kind": "googletts", "enabled": True},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert len(chain) == 2
    assert sum(1 for e in chain if e.kind == "googletts") == 1
    # Existing enabled flag preserved (not overwritten with a disabled dup).
    assert chain[-1].enabled is True


def test_migrate_expression_audio_chain_enabled_flag_defaults_true():
    """Missing 'enabled' key in a JSON dict defaults to True."""
    data = {
        "expression_audio_chain": [
            {"kind": "pack", "pack_id": "nhk16"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    assert result["expression_audio_chain"][0].enabled is True


def test_migrate_expression_audio_chain_pack_id_none():
    """pack_id absent from JSON dict is stored as None."""
    data = {
        "expression_audio_chain": [
            {"kind": "jpod101"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    assert result["expression_audio_chain"][0].pack_id is None


def test_migrate_expression_audio_chain_skips_unknown_kinds():
    """Entries with unknown kind values are silently dropped.

    A disabled googletts entry is appended since the source chain had none.
    """
    data = {
        "expression_audio_chain": [
            {"kind": "unknown_source", "pack_id": "foo"},
            {"kind": "jpod101"},
        ]
    }
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    chain = result["expression_audio_chain"]
    assert len(chain) == 2
    assert chain[0].kind == "jpod101"
    assert chain[1] == AudioSourceEntry(kind="googletts", enabled=False)


def test_migrate_expression_audio_chain_absent_key_is_noop():
    """When expression_audio_chain is absent the dict is returned unchanged."""
    data: dict = {"anki_deck_name": "Test"}
    result = GUIConfigManager._migrate_expression_audio_chain(data)
    assert "expression_audio_chain" not in result

"""Tests for dictionary_chain persistence and legacy migration."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, ChainEntry
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
    """A saved config missing expression_audio_enabled and expression_audio_delay
    must fall through to dataclass defaults rather than crashing.
    """
    tmp_config.write_text(
        json.dumps(
            {
                "anki_deck_name": "MyDeck",
                # expression_audio_enabled / expression_audio_delay absent
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.anki_deck_name == "MyDeck"
    assert loaded.expression_audio_enabled is False
    assert loaded.expression_audio_delay == 0.2


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

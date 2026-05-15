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


def test_legacy_use_offline_true_synthesizes_default_chain(tmp_config: Path):
    """An old gui_config.json with use_offline_dict=True but no dictionary_chain
    should yield [jmdict-english(enabled), jisho(enabled)]."""
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
        ChainEntry(kind="jisho", dict_id=None, enabled=True),
    )


def test_legacy_use_offline_false_disables_jmdict(tmp_config: Path):
    tmp_config.write_text(
        json.dumps(
            {
                "use_offline_dict": False,
            }
        )
    )

    loaded = GUIConfigManager.load_config()
    assert loaded.dictionary_chain == (
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
        ChainEntry(kind="jisho", dict_id=None, enabled=True),
    )

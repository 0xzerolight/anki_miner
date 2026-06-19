"""ui_language config field: default, normalization, persistence round-trip."""

import json
from dataclasses import replace

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager


def test_default_ui_language_is_en():
    assert AnkiMinerConfig().ui_language == "en"


def test_ui_language_normalized():
    assert AnkiMinerConfig(ui_language="  FR ").ui_language == "fr"
    assert AnkiMinerConfig(ui_language="").ui_language == "en"


def test_ui_language_round_trips(tmp_path, monkeypatch):
    # ANKI_MINER_HOME is already redirected to a tmp dir by conftest; persist + reload.
    GUIConfigManager.save_config(replace(create_default_config(), ui_language="fr"))
    assert GUIConfigManager.load_config().ui_language == "fr"


def test_legacy_config_without_ui_language_defaults_to_en():
    cfg_path = GUIConfigManager.CONFIG_FILE
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert GUIConfigManager.load_config().ui_language == "en"

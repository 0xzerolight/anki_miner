"""The mining-language config field and its duplicated-literal sync guard."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages import AVAILABLE_LANGUAGES


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


def test_language_defaults_to_ja():
    assert AnkiMinerConfig().language == "ja"


def test_config_literal_matches_available_languages():
    """config must not import the languages package, so the tuple is a
    hand-duplicated literal; this assertion keeps the two in sync."""
    assert _LANGUAGE_CODES == AVAILABLE_LANGUAGES


def test_unknown_language_resets_to_ja():
    assert AnkiMinerConfig(language="tlh").language == "ja"


def test_language_is_normalized():
    assert AnkiMinerConfig(language="  ZH ").language == "zh"


def test_language_survives_replace():
    assert dataclasses.replace(AnkiMinerConfig(), language="ko").language == "ko"


def test_language_round_trips_through_json(isolated_config_file):
    GUIConfigManager.save_config(dataclasses.replace(create_default_config(), language="zh"))
    assert GUIConfigManager.load_config().language == "zh"


def test_reset_to_defaults_keeps_the_mining_language(test_config, qtbot, monkeypatch):
    """`language_stash` is machine-specific, so Reset to Defaults preserves it.
    Resetting `language` alongside it would leave the stash holding a parked
    snapshot for the language now active, which the field's invariant forbids
    ("every language that is NOT active")."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui.widgets.settings_tab import SettingsTab
    from anki_miner.languages import registry

    # The settings panels resolve the active language's capabilities as they
    # load (gui/utils/language_gate.py), and zh has no registered profile until
    # Stage 2A. Register a ja clone under "zh" for this test only; the cache is
    # swapped for a copy first so the stub cannot leak into another test. The
    # clone is built out here, not inside the builder: get_profile holds a plain
    # (non-reentrant) lock while it calls one, so a builder that re-enters it
    # deadlocks.
    ja_profile = registry.get_profile("ja")
    monkeypatch.setattr(registry, "_CACHE", dict(registry._CACHE))
    monkeypatch.setitem(registry._BUILDERS, "zh", lambda: dataclasses.replace(ja_profile, code="zh"))

    tab = SettingsTab(
        dataclasses.replace(test_config, language="zh", language_stash={"ja": {"anki_deck_name": "JA"}}),
    )
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    emitted: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emitted.append)

    tab._on_reset_to_defaults_clicked()

    assert emitted[-1].language == "zh"
    assert emitted[-1].language not in emitted[-1].language_stash


def test_old_build_drops_the_key_without_raising(isolated_config_file):
    """Downgrade simulation: an unknown key is dropped by the valid-keys filter
    in _migrate_dict, exactly as `language` would be on a pre-Stage-0 build."""
    payload = {"language": "zh", "language_of_the_future": "xx"}
    migrated = GUIConfigManager._migrate_dict(payload)
    assert "language_of_the_future" not in migrated
    assert AnkiMinerConfig(**migrated).language == "zh"
    isolated_config_file.write_text(json.dumps({"language_of_the_future": "xx"}), encoding="utf-8")
    assert GUIConfigManager.load_config().language == "ja"

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


def test_an_unregistered_language_degrades_to_ja():
    """`_LANGUAGE_CODES` whitelists zh/ko before their profiles exist, so a
    hand-edited config carries a code the registry cannot build. Pre-1B the
    field was inert; degrading here keeps it inert instead of raising out of
    every `get_profile(config_language(config))` site."""
    from anki_miner.languages.registry import available_languages, config_language

    assert "zh" not in available_languages()
    assert config_language(AnkiMinerConfig(language="zh")) == "ja"


def test_a_registered_language_is_returned_verbatim(monkeypatch):
    """Self-heals the moment Stage 2A registers the real profile."""
    from anki_miner.languages.registry import config_language
    from tests.unit.languages.stub_registry import register_stub_profile

    register_stub_profile(monkeypatch, "zh")
    assert config_language(AnkiMinerConfig(language="zh")) == "zh"


def test_the_degrade_is_logged_once_per_code(caplog, monkeypatch):
    from anki_miner.languages import registry

    monkeypatch.setattr(registry, "_DEGRADE_WARNED", set())
    with caplog.at_level("WARNING", logger="anki_miner.languages.registry"):
        for _ in range(3):
            registry.config_language(AnkiMinerConfig(language="zh"))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "zh" in warnings[0].getMessage()


def test_settings_panels_load_an_unregistered_language(test_config, qtbot):
    """The finding's crash site: `load_from_config` resolves the profile's
    capabilities, and an unbuilt code used to raise ValueError with no in-app
    recovery."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
    from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel

    cfg = dataclasses.replace(test_config, language="zh")
    # Held in a list: qtbot.addWidget keeps only a weakref.
    panels = [FilteringSettingsPanel(), AnkiSettingsPanel()]
    for panel in panels:
        qtbot.addWidget(panel)
        panel.load_from_config(cfg)


def test_anki_service_accepts_an_unregistered_language(test_config):
    from anki_miner.services.anki_service import AnkiService

    assert AnkiService(dataclasses.replace(test_config, language="zh")) is not None


def test_old_build_drops_the_key_without_raising(isolated_config_file):
    """Downgrade simulation: an unknown key is dropped by the valid-keys filter
    in _migrate_dict, exactly as `language` would be on a pre-Stage-0 build."""
    payload = {"language": "zh", "language_of_the_future": "xx"}
    migrated = GUIConfigManager._migrate_dict(payload)
    assert "language_of_the_future" not in migrated
    assert AnkiMinerConfig(**migrated).language == "zh"
    isolated_config_file.write_text(json.dumps({"language_of_the_future": "xx"}), encoding="utf-8")
    assert GUIConfigManager.load_config().language == "ja"

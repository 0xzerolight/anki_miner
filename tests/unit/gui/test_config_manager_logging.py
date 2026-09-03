"""Diagnostic logging of config saves, migrations, import/export and switches.

Three support questions run through these lines: "my settings don't stick",
"a setting changed by itself", and a config file that a schema shim silently
rewrote. Answering any of them needs the WRITE receipt (which path, which
profile marker, which fields moved), not the resulting file — by the time a
user reports it, the file only shows the end state.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.controllers.profile_controller import ProfileController
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore
from tests.unit.test_profile_controller import _FakeWindow

_CONFIG_LOGGER = "anki_miner.gui.utils.config_manager"
_PROFILE_LOGGER = "anki_miner.gui.controllers.profile_controller"


def _messages(caplog, prefix: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def _one(caplog, prefix: str) -> str:
    found = _messages(caplog, prefix)
    assert len(found) == 1, f"expected exactly one {prefix!r} line, got {found}"
    return found[0]


class TestSaveReceipt:
    def test_first_save_reports_the_path_schema_profile_and_field_count(self, test_config, caplog):
        GUIConfigManager.ACTIVE_PROFILE_ID = "work"
        try:
            with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
                GUIConfigManager.save_config(test_config)
        finally:
            GUIConfigManager.ACTIVE_PROFILE_ID = None

        line = _one(caplog, "Config saved:")
        assert f"path={GUIConfigManager.CONFIG_FILE}" in line
        assert f"schema={GUIConfigManager.CONFIG_SCHEMA_VERSION}" in line
        assert "profile=work" in line
        assert "changed=first_save" in line
        assert re.search(r"fields=\d\d+", line), line

    def test_second_save_names_only_the_changed_field(self, test_config, caplog):
        GUIConfigManager.save_config(test_config)

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            GUIConfigManager.save_config(replace(test_config, anki_deck_name="Mining2"))

        line = _one(caplog, "Config saved:")
        assert "changed=anki_deck_name=Mining2" in line
        assert "fields=1" in line
        assert "anki_note_type" not in line

    def test_an_unchanged_resave_reports_no_fields(self, test_config, caplog):
        GUIConfigManager.save_config(test_config)

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            GUIConfigManager.save_config(test_config)

        line = _one(caplog, "Config saved:")
        assert "changed=-" in line
        assert "fields=0" in line

    def test_a_container_field_is_named_without_its_contents(self, test_config, caplog):
        GUIConfigManager.save_config(test_config)
        changed = replace(test_config, anki_tags=("mined", "anime"))

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            GUIConfigManager.save_config(changed)

        line = _one(caplog, "Config saved:")
        assert "changed=anki_tags" in line
        assert "mined" not in line

    def test_the_diff_does_not_span_two_different_config_files(self, test_config, tmp_path, caplog, monkeypatch):
        GUIConfigManager.save_config(test_config)
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "other_gui_config.json")

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            GUIConfigManager.save_config(replace(test_config, anki_deck_name="Mining2"))

        assert "changed=first_save" in _one(caplog, "Config saved:")


class TestMigrationReceipt:
    def test_a_schema_2_dict_reports_the_versions_and_the_shims_that_fired(self, caplog):
        raw = {
            "config_schema_version": 2,
            "anki_deck_name": "Deck",
            "auto_update_ytdlp": True,
            "use_native_file_dialogs": False,
            "long_dead_field": 1,
        }

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            migrated = GUIConfigManager._migrate_dict(
                raw,
                seed_wordsets=True,
                disable_legacy_ytdlp_update=True,
                enable_native_file_dialogs=True,
                seed_first_run_flags=True,
            )

        line = _one(caplog, "Config migrated:")
        assert "from=2" in line
        assert f"to={GUIConfigManager.CONFIG_SCHEMA_VERSION}" in line
        assert "disable_legacy_ytdlp_update" in line
        assert "enable_native_file_dialogs" in line
        assert "seed_wordsets" not in line
        assert "dropped_keys=long_dead_field" in line
        assert migrated["auto_update_ytdlp"] is False

    def test_a_current_schema_dict_with_nothing_to_do_stays_quiet(self, caplog):
        raw = {
            "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
            "anki_deck_name": "Deck",
            "first_run_setup_done": True,
            "first_run_shortcut_done": True,
        }

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            GUIConfigManager._migrate_dict(raw, seed_first_run_flags=True)

        assert _messages(caplog, "Config migrated:") == []


class TestExportImportReceipts:
    def test_export_reports_the_path_and_what_it_carried(self, test_config, tmp_path, caplog):
        target = tmp_path / "settings-export.json"

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            GUIConfigManager.export_config(test_config, target)

        line = _one(caplog, "Config exported:")
        assert f"path={target}" in line
        assert "excluded=" in line

    def test_import_reports_the_path_and_the_rejected_fields(self, test_config, tmp_path, caplog):
        target = tmp_path / "settings-export.json"
        GUIConfigManager.export_config(test_config, target)
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["settings"]["anki_deck_name"] = "Imported"
        payload["settings"]["subtitle_offset"] = "not-a-number"
        target.write_text(json.dumps(payload), encoding="utf-8")

        with caplog.at_level(logging.INFO, logger=_CONFIG_LOGGER):
            result = GUIConfigManager.import_config(target, test_config)

        line = _one(caplog, "Config imported:")
        assert f"path={target}" in line
        assert "invalid=subtitle_offset" in line
        assert result.config.anki_deck_name == "Imported"
        record = next(r for r in caplog.records if r.getMessage().startswith("Config imported:"))
        assert record.levelno == logging.WARNING


class TestProfileSwitchReceipt:
    @pytest.fixture(autouse=True)
    def _no_repolish(self, monkeypatch, qapp):
        monkeypatch.setattr(Theme, "apply_to_app", classmethod(lambda cls, app, mode=None: None))

    def test_a_durable_switch_logs_the_ids_and_the_display_name(self, test_config, caplog):
        outgoing = replace(test_config, anki_deck_name="Deck A")
        incoming = replace(test_config, anki_deck_name="Deck B")
        ProfileStore.write_profile("a", outgoing, name="A")
        ProfileStore.write_profile("b", incoming, name="B")
        GUIConfigManager.ACTIVE_PROFILE_ID = "a"
        GUIConfigManager.save_config(outgoing)
        window = _FakeWindow(outgoing)
        controller = ProfileController(window)  # type: ignore[arg-type]

        try:
            with caplog.at_level(logging.INFO, logger=_PROFILE_LOGGER):
                result = controller.switch_to("b")
        finally:
            GUIConfigManager.ACTIVE_PROFILE_ID = None

        assert result.switched
        line = _one(caplog, "Profile switched:")
        assert "from=a" in line
        assert "to=b" in line
        assert "name=B" in line


def test_config_manager_module_uses_a_real_config_path(test_config: AnkiMinerConfig) -> None:
    """Guard: the isolated home must be in play, or these tests write the real one."""
    assert isinstance(GUIConfigManager.CONFIG_FILE, Path)
    assert "anki_miner" in str(GUIConfigManager.CONFIG_FILE)
    assert create_default_config() is not None

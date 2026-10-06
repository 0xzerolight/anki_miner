"""--api settings-import: the command line, the lock and the verdict."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from anki_miner.cli import api, entry
from anki_miner.config import paths as config_paths
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore


@pytest.fixture
def verdict(capfd, monkeypatch):
    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)

    def run(*argv: str) -> dict:
        assert entry.main(["--api", *argv]) == 0
        [line] = capfd.readouterr().out.splitlines()
        return json.loads(line)

    return run


@pytest.fixture
def settings_file(tmp_path, test_config):
    GUIConfigManager.save_config(replace(test_config, anki_deck_name="Live deck"))
    path = tmp_path / "surasura.json"
    path.write_text(json.dumps({"anki_deck_name": "Surasura"}), encoding="utf-8")
    return str(path)


def test_settings_import_verdict(verdict, settings_file) -> None:
    v = verdict("settings-import", settings_file, "--language", "ja", "--name", "Surasura")
    assert v["ok"] is True and v["command"] == "settings-import"
    assert v["result"] == {"profile": "surasura", "created": True, "invalid_fields": []}


def test_name_and_profile_together_are_bad_arguments(verdict, settings_file) -> None:
    v = verdict("settings-import", settings_file, "--language", "ja", "--name", "A", "--profile", "b")
    assert v["error"] == "BAD_ARGUMENTS" and v["command"] == "settings-import"


def test_busy_while_any_window_is_open(verdict, settings_file) -> None:
    from anki_miner.gui.app import _hold_window_marker

    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    assert marker is not None
    try:
        v = verdict("settings-import", settings_file, "--language", "ja", "--name", "Surasura")
    finally:
        marker.unlock()
    assert v["error"] == "BUSY" and "window is open" in v["message"]
    assert not ProfileStore.profiles_dir().exists()


def test_version_lists_the_command_and_feature(verdict) -> None:
    result = verdict("version")["result"]
    assert "settings-import" in result["commands"] and "settings-import" in result["features"]

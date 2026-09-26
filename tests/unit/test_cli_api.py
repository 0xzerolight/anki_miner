"""``--api``: verdict line, dispatch and the commands that need no run folder."""

from __future__ import annotations

import json

import pytest

from anki_miner.cli import api, entry
from anki_miner.cli.api import contract


@pytest.fixture
def verdict(capfd, monkeypatch):
    # No process boot and no root log handler: the sink would outlive the test
    # (tests/unit/test_child_logging.py cleans up by hand for the same reason);
    # the e2e test proves the API log lands in the home.
    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)

    def run(*argv: str) -> dict:
        assert entry.main(["--api", *argv]) == 0
        [line] = capfd.readouterr().out.splitlines()
        return json.loads(line)

    return run


def test_version(verdict) -> None:
    from anki_miner import __version__

    v = verdict("version")
    assert v == {
        "schema": 1,
        "command": "version",
        "ok": True,
        "error": None,
        "message": None,
        "result": {"schema": 1, "app": __version__, "commands": list(contract.COMMANDS), "features": []},
    }


def test_unknown_command_is_bad_arguments(verdict) -> None:
    v = verdict("frobnicate")
    assert v["ok"] is False and v["error"] == "BAD_ARGUMENTS" and v["command"] is None and v["runs"] == []


def test_internal_error_is_a_verdict_not_a_traceback(verdict, monkeypatch) -> None:
    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api, "_dispatch", boom)
    v = verdict("version")
    assert v["error"] == "INTERNAL" and "kaboom" in v["message"]


def test_api_flag_is_in_the_launch_dispatch() -> None:
    from anki_miner.gui import launch

    assert "--api" in entry.COMMANDS and launch.CLI_COMMANDS == entry.COMMANDS


def test_setup_failure_maps_unreachable_anki() -> None:
    import requests

    from anki_miner.exceptions import AnkiConnectionError

    try:
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from requests.ConnectionError()
    except AnkiConnectionError as exc:
        assert contract.setup_failure(exc).code == "ANKI_UNREACHABLE"
    assert contract.setup_failure(AnkiConnectionError("AnkiConnect error in 'x': y")).code == "SETUP_ERROR"


def test_profiles_command(verdict) -> None:
    v = verdict("profiles")
    assert v["ok"] and v["result"] == {"profiles": [{"id": "default", "name": "Default", "active": True}]}


def _fake_validation(**checks):
    return type(
        "V", (), {"__init__": lambda self, config: None, **{k: (lambda self, r=r: r) for k, r in checks.items()}}
    )


def test_check_reports_every_item(verdict, monkeypatch, test_config) -> None:
    from anki_miner.cli.api import commands

    monkeypatch.setattr(commands.settings, "load_profile_config", lambda pid: test_config)
    fake = _fake_validation(
        check_ankiconnect=(True, ""),
        check_deck_exists=(False, "Deck 'x' not found."),
        check_note_type_exists=(True, ""),
        check_field_names=(True, ""),
        check_offline_dictionary=(True, ""),
    )
    monkeypatch.setattr(commands, "ValidationService", fake)
    monkeypatch.setattr(commands, "stale_resource_reimport_error", lambda config: None)
    monkeypatch.setattr(commands, "binary_available", lambda resolved: True)
    v = verdict("check", "--language", "ja")
    items = {i["name"]: i for i in v["result"]["items"]}
    assert v["result"]["ready"] is False
    assert list(items) == [
        "anki",
        "deck",
        "note_type",
        "fields",
        "dictionary",
        "resources",
        "language_pack",
        "ffmpeg",
        "ffprobe",
    ]
    assert items["deck"] == {"name": "deck", "ok": False, "message": "Deck 'x' not found."}
    assert items["anki"]["message"] is None


def test_check_skips_anki_items_when_unreachable(verdict, monkeypatch, test_config) -> None:
    from anki_miner.cli.api import commands

    monkeypatch.setattr(commands.settings, "load_profile_config", lambda pid: test_config)
    fake = _fake_validation(check_ankiconnect=(False, "Cannot connect"), check_offline_dictionary=(True, ""))
    monkeypatch.setattr(commands, "ValidationService", fake)
    monkeypatch.setattr(commands, "stale_resource_reimport_error", lambda config: None)
    monkeypatch.setattr(commands, "binary_available", lambda resolved: True)
    items = {i["name"]: i for i in verdict("check", "--language", "ja")["result"]["items"]}
    assert items["deck"]["ok"] is False and "not reachable" in items["deck"]["message"]


def test_check_unknown_language_is_bad_arguments(verdict) -> None:
    assert verdict("check", "--language", "xx")["error"] == "BAD_ARGUMENTS"


def test_settings_export_marks_unconfigured_language(verdict, tmp_path, test_config) -> None:
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    GUIConfigManager.save_config(test_config)
    out = tmp_path / "ko.json"
    v = verdict("settings-export", "--language", "ko", "--out", str(out))
    assert v["ok"] is True
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["configured"] is False and data["settings"]["language"] == "ko"
    assert data["anki_miner_settings"] == 1


def test_settings_export_bad_out_folder(verdict, tmp_path) -> None:
    v = verdict("settings-export", "--language", "ja", "--out", str(tmp_path / "no" / "x.json"))
    assert v["error"] == "BAD_ARGUMENTS"

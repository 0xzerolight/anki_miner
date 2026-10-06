"""``--api``: verdict line, dispatch and the commands that need no run folder."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

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
        "result": {
            "schema": 1,
            "app": __version__,
            "commands": list(contract.COMMANDS),
            "features": list(contract.FEATURES),
        },
    }
    assert {"sentence-rules-off", "bold-target"} <= set(v["result"]["features"])
    assert "beside-window" in v["result"]["features"]


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


def _check_ok(monkeypatch, commands, **overrides) -> None:
    """Every check passing, except *overrides*; yt-dlp and the speech model missing."""
    checks = {
        "check_ankiconnect": (True, ""),
        "check_deck_exists": (True, ""),
        "check_note_type_exists": (True, ""),
        "check_field_names": (True, ""),
        "check_offline_dictionary": (True, ""),
        **overrides,
    }
    monkeypatch.setattr(commands, "ValidationService", _fake_validation(**checks))
    monkeypatch.setattr(commands, "stale_resource_reimport_error", lambda config: None)
    monkeypatch.setattr(commands, "binary_available", lambda resolved: True)
    monkeypatch.setattr(commands, "ytdlp_available", lambda config: False)
    monkeypatch.setattr(commands, "usable_model_installed", lambda config: False)


def test_check_reports_every_item(verdict, monkeypatch, test_config) -> None:
    from anki_miner.cli.api import commands

    monkeypatch.setattr(commands.settings, "load_profile_config", lambda pid: test_config)
    _check_ok(monkeypatch, commands, check_deck_exists=(False, "Deck 'x' not found."))
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
        "yt_dlp",
        "speech_model",
    ]
    assert items["deck"] == {"name": "deck", "ok": False, "message": "Deck 'x' not found."}
    assert items["anki"]["message"] is None and "yt-dlp" in items["yt_dlp"]["message"]


def test_check_skips_anki_items_when_unreachable(verdict, monkeypatch, test_config) -> None:
    from anki_miner.cli.api import commands

    monkeypatch.setattr(commands.settings, "load_profile_config", lambda pid: test_config)
    _check_ok(monkeypatch, commands, check_ankiconnect=(False, "Cannot connect"))
    items = {i["name"]: i for i in verdict("check", "--language", "ja")["result"]["items"]}
    assert items["deck"]["ok"] is False and "not reachable" in items["deck"]["message"]


def test_a_missing_speech_model_does_not_count_toward_ready(verdict, monkeypatch, test_config) -> None:
    from anki_miner.cli.api import commands

    assert test_config.youtube_subtitle_source == "auto"
    monkeypatch.setattr(commands.settings, "load_profile_config", lambda pid: test_config)
    _check_ok(monkeypatch, commands)
    result = verdict("check", "--language", "ja")["result"]
    items = {i["name"]: i for i in result["items"]}
    assert items["yt_dlp"]["ok"] is False and items["speech_model"]["ok"] is False
    assert "speech model" in items["speech_model"]["message"]
    assert result["ready"] is True


def test_fetch_items_do_not_count_toward_ready(verdict, monkeypatch, test_config) -> None:
    from anki_miner.cli.api import commands

    monkeypatch.setattr(
        commands.settings, "load_profile_config", lambda pid: replace(test_config, youtube_subtitle_source="captions")
    )
    _check_ok(monkeypatch, commands)
    result = verdict("check", "--language", "ja")["result"]
    assert result["ready"] is True
    assert [i["name"] for i in result["items"]][-1] == "yt_dlp"  # captions only: no speech model needed


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


def _write_run_file(tmp_path) -> str:
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_dir": str(tmp_path),
                "language": "ja",
                "episodes": [{"run_id": "e", "video_file": "v", "subtitle_file": "s", "words": [{"word": "x"}]}],
            }
        ),
        encoding="utf-8",
    )
    return str(run)


def test_mine_runs_beside_an_idle_window(verdict, tmp_path, monkeypatch) -> None:
    from anki_miner.cli.api import runs
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    monkeypatch.setattr(
        runs,
        "mine_runs",
        lambda job, cancel: [{"run_id": "e", "ok": True, "error": None, "message": None, "file": "result-1.json"}],
    )
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    assert marker is not None
    try:
        v = verdict("mine", _write_run_file(tmp_path))
    finally:
        marker.unlock()
    assert v["ok"] is True and v["runs"][0]["file"] == "result-1.json"


def test_mine_busy_while_the_window_mines(verdict, tmp_path) -> None:
    import os

    from PyQt6.QtCore import QLockFile

    from anki_miner.config import paths as config_paths

    run = _write_run_file(tmp_path)
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    mining = QLockFile(str(config_paths.ANKI_MINER_HOME / f"instance.mining-{os.getpid()}.lock"))
    assert mining.tryLock(0)
    try:
        v = verdict("mine", run)
    finally:
        mining.unlock()
    assert v["error"] == "BUSY" and "mining" in v["message"] and v["runs"] == []
    assert not (tmp_path / "e").exists()  # refused before any run folder


def test_mine_runs_under_the_lock_and_reports_per_run(verdict, tmp_path, monkeypatch) -> None:
    from anki_miner.cli.api import runs

    seen = {}

    def fake_mine(job, cancel):
        seen["job"] = job
        return [{"run_id": "e", "ok": False, "error": "VIDEO_UNREADABLE", "message": "x", "file": None}]

    monkeypatch.setattr(runs, "mine_runs", fake_mine)
    v = verdict("mine", _write_run_file(tmp_path))
    assert v["command"] == "mine" and v["ok"] is False and v["error"] is None
    assert v["runs"][0]["error"] == "VIDEO_UNREADABLE"
    assert seen["job"].episodes[0].run_id == "e"


def test_a_dry_run_takes_no_lock(verdict, tmp_path, monkeypatch) -> None:
    from anki_miner.cli.api import runs
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    run = tmp_path / "run.json"
    data = json.loads(Path(_write_run_file(tmp_path)).read_text(encoding="utf-8"))
    run.write_text(json.dumps({**data, "dry_run": True}), encoding="utf-8")
    seen = {}

    def fake_mine(job, cancel, kind=None):
        seen["kind"] = kind
        return [{"run_id": "e", "ok": True, "error": None, "message": None, "file": "result-1.json"}]

    monkeypatch.setattr(runs, "mine_runs", fake_mine)
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    try:
        assert verdict("mine", str(run))["ok"] is True
    finally:
        marker.unlock()
    assert seen["kind"] is runs.Kind.DRY_RUN


def test_render_takes_no_lock_and_refuses_a_dry_run(verdict, tmp_path, monkeypatch) -> None:
    from anki_miner.cli.api import runs
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    kinds = []
    monkeypatch.setattr(
        runs,
        "mine_runs",
        lambda job, cancel, kind=None: kinds.append(kind)
        or [{"run_id": "e", "ok": True, "error": None, "message": None, "file": "render-1.json"}],
    )
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)  # the window is open: mine would be BUSY
    try:
        assert verdict("render", _write_run_file(tmp_path))["ok"] is True and kinds == [runs.Kind.RENDER]
    finally:
        marker.unlock()
    run = tmp_path / "run.json"
    run.write_text(json.dumps({**json.loads(run.read_text(encoding="utf-8")), "dry_run": True}), encoding="utf-8")
    assert verdict("render", str(run))["error"] == "BAD_RUN_FILE"


def test_bad_run_file_is_refused_before_the_lock(verdict, tmp_path) -> None:
    bad = tmp_path / "run.json"
    bad.write_text("{}", encoding="utf-8")
    assert verdict("mine", str(bad))["error"] == "BAD_RUN_FILE"

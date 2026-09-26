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

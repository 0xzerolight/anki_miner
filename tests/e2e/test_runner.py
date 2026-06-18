"""Unit tests for the E2E harness CLI runner (``tests/e2e/runner.py``).

No live Anki / no network: gateway calls are patched at the soak / gateway level.
These tests assert:

* ``ForeignDeckError`` raised by ``_maybe_gateway`` (via ``ensure_test_deck``) is
  caught by both ``_cmd_smoke`` and ``_cmd_soak``, surfaces a clean one-line
  ``ERROR:`` message on stderr, produces no traceback, and returns exit code 2.
* ``cleanup --allow-existing-deck`` passes ``allow_existing=True`` to
  ``ensure_test_deck`` and then calls ``delete_test_deck``.
* ``cleanup`` (without the flag) does NOT call ``ensure_test_deck``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.e2e.anki_gateway import ForeignDeckError
from tests.e2e.config import E2EConfig
from tests.e2e.runner import _cmd_cleanup, _cmd_smoke, _cmd_soak, _foreign_deck, main

# Patch target for run_inprocess_soak / run_crossprocess_soak inside runner.
_RUNNER_INPROCESS = "tests.e2e.runner.run_inprocess_soak"
_RUNNER_CROSSPROCESS = "tests.e2e.runner.run_crossprocess_soak"
# Patch target for AnkiGateway as used inside runner._cmd_cleanup.
_RUNNER_GATEWAY = "tests.e2e.runner.AnkiGateway"
# Patch target for set_test_home (runner calls it; avoid real home side effects).
_RUNNER_SET_HOME = "tests.e2e.runner.set_test_home"
# Patch target for RunDir so we don't need a real runs_root on disk.
_RUNNER_RUNDIR = "tests.e2e.runner.RunDir"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(command: str, **kwargs):
    """Build a minimal argparse.Namespace for a given command."""
    import argparse

    defaults = {
        "command": command,
        "home": None,
        "deck": None,
        "ankiconnect_url": None,
        "timeout": None,
    }
    if command == "smoke":
        pass  # no extra defaults needed
    if command == "soak":
        defaults.update(
            {
                "mode": "inprocess",
                "sessions": 1,
                "preview": False,
                "bypass_known_words": True,
                "policy": "all",
                "first_n": 0,
            }
        )
    if command == "cleanup":
        defaults["allow_existing_deck"] = False
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _fake_soak_report():
    """Return a minimal SoakReport substitute that makes _emit happy."""
    from tests.e2e.soak import SoakReport

    return SoakReport(verdict="PASS", divergence={"verdict": "PASS"})


# ---------------------------------------------------------------------------
# _foreign_deck helper
# ---------------------------------------------------------------------------


def test_foreign_deck_prints_actionable_message(capsys):
    """_foreign_deck prints an ERROR: line mentioning the deck name and cleanup cmd."""
    e2e = E2EConfig()
    code = _foreign_deck(e2e)
    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert e2e.deck_name in captured.err
    assert "cleanup" in captured.err
    assert not captured.out  # nothing on stdout


# ---------------------------------------------------------------------------
# _cmd_smoke: ForeignDeckError → clean exit 2
# ---------------------------------------------------------------------------


def test_cmd_smoke_foreign_deck_clean_exit(tmp_path, capsys, monkeypatch):
    """_cmd_smoke catches ForeignDeckError, prints ERROR: to stderr, returns 2.

    The gateway is patched at the soak level: run_inprocess_soak raises
    ForeignDeckError (the real propagation path from _maybe_gateway).
    """
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("smoke")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, side_effect=ForeignDeckError("deck has 3 notes")),
    ):
        code = _cmd_smoke(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "cleanup" in captured.err
    # No traceback: stderr should be a single line (no 'Traceback' keyword).
    assert "Traceback" not in captured.err


def test_cmd_smoke_foreign_deck_no_stdout(tmp_path, capsys, monkeypatch):
    """ForeignDeckError in smoke writes nothing to stdout (preserves machine contract)."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("smoke")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, side_effect=ForeignDeckError("leftover")),
    ):
        code = _cmd_smoke(args)

    captured = capsys.readouterr()
    assert code == 2
    assert not captured.out


# ---------------------------------------------------------------------------
# _cmd_soak: ForeignDeckError → clean exit 2
# ---------------------------------------------------------------------------


def test_cmd_soak_foreign_deck_clean_exit(tmp_path, capsys, monkeypatch):
    """_cmd_soak catches ForeignDeckError from the soak runner, returns 2 cleanly."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, side_effect=ForeignDeckError("deck has prior notes")),
    ):
        code = _cmd_soak(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "cleanup" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_cmd_soak_foreign_deck_crossprocess(tmp_path, capsys, monkeypatch):
    """_cmd_soak catches ForeignDeckError from the cross-process runner too."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", mode="crossprocess")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_CROSSPROCESS, side_effect=ForeignDeckError("deck has prior notes")),
    ):
        code = _cmd_soak(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# main(): ForeignDeckError via smoke subcommand → exit 2
# ---------------------------------------------------------------------------


def test_main_smoke_foreign_deck_exit_code(tmp_path, monkeypatch, capsys):
    """main(['smoke']) propagates ForeignDeckError to exit code 2 without crashing."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    with (
        patch("tests.e2e.runner._ensure_qapplication"),
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, side_effect=ForeignDeckError("deck blocked")),
    ):
        code = main(["smoke"])

    assert code == 2
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err


# ---------------------------------------------------------------------------
# cleanup --allow-existing-deck: passes allow_existing=True to ensure_test_deck
# ---------------------------------------------------------------------------


def test_cmd_cleanup_allow_existing_deck_calls_ensure(tmp_path, monkeypatch):
    """--allow-existing-deck causes _cmd_cleanup to call ensure_test_deck(allow_existing=True)."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("cleanup", allow_existing_deck=True)

    mock_gw = MagicMock()
    mock_gw.ping.return_value = "6"
    mock_gw.config = E2EConfig()

    with patch(_RUNNER_GATEWAY, return_value=mock_gw):
        code = _cmd_cleanup(args)

    assert code == 0
    mock_gw.ensure_test_deck.assert_called_once_with(allow_existing=True)
    mock_gw.delete_test_deck.assert_called_once()


def test_cmd_cleanup_without_flag_skips_ensure(tmp_path, monkeypatch):
    """cleanup without --allow-existing-deck must NOT call ensure_test_deck."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("cleanup", allow_existing_deck=False)

    mock_gw = MagicMock()
    mock_gw.ping.return_value = "6"
    mock_gw.config = E2EConfig()

    with patch(_RUNNER_GATEWAY, return_value=mock_gw):
        code = _cmd_cleanup(args)

    assert code == 0
    mock_gw.ensure_test_deck.assert_not_called()
    mock_gw.delete_test_deck.assert_called_once()


# ---------------------------------------------------------------------------
# parser: --allow-existing-deck is accepted by cleanup subcommand
# ---------------------------------------------------------------------------


def test_parser_cleanup_allow_existing_deck_flag():
    """argparse accepts --allow-existing-deck on the cleanup subcommand."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["cleanup", "--allow-existing-deck"])
    assert args.allow_existing_deck is True


def test_parser_cleanup_no_flag_default():
    """Without --allow-existing-deck, allow_existing_deck defaults to False."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["cleanup"])
    assert args.allow_existing_deck is False

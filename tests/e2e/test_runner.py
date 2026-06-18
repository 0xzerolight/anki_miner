"""Unit tests for the E2E harness CLI runner (``tests/e2e/runner.py``).

No live Anki / no network: gateway calls are patched at the soak / gateway level.
These tests assert:

* ``ForeignDeckError`` raised by ``_maybe_gateway`` (via ``ensure_test_deck``) is
  caught by both ``_cmd_smoke`` and ``_cmd_soak``, surfaces a clean one-line
  ``ERROR:`` message on stderr, produces no traceback, and returns exit code 2.
* ``cleanup`` calls ``delete_test_deck`` directly (no ``ensure_test_deck`` call).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.e2e.anki_gateway import AnkiUnreachableError, ForeignDeckError
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
        "fresh_home": False,
    }
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
# _cmd_smoke / _cmd_soak: AnkiUnreachableError → clean exit 2 (no traceback)
# ---------------------------------------------------------------------------


def test_cmd_smoke_anki_down_clean_exit(tmp_path, capsys, monkeypatch):
    """_cmd_smoke catches AnkiUnreachableError (from _maybe_gateway), returns 2.

    The soak runner raises AnkiUnreachableError (now that _maybe_gateway re-raises
    instead of swallowing it).  The runner's existing except clause must produce a
    single-line ERROR: on stderr with no traceback and nothing on stdout.
    """
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("smoke")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, side_effect=AnkiUnreachableError("connection refused")),
    ):
        code = _cmd_smoke(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_cmd_soak_inprocess_anki_down_clean_exit(tmp_path, capsys, monkeypatch):
    """_cmd_soak (inprocess) catches AnkiUnreachableError, returns 2 cleanly."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", mode="inprocess")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, side_effect=AnkiUnreachableError("connection refused")),
    ):
        code = _cmd_soak(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_cmd_soak_crossprocess_anki_down_clean_exit(tmp_path, capsys, monkeypatch):
    """_cmd_soak (crossprocess) catches AnkiUnreachableError, returns 2 cleanly."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", mode="crossprocess")

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_CROSSPROCESS, side_effect=AnkiUnreachableError("connection refused")),
    ):
        code = _cmd_soak(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


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
# cleanup: simple delete path (no ensure_test_deck call)
# ---------------------------------------------------------------------------


def test_cmd_cleanup_calls_delete(tmp_path, monkeypatch, capsys):
    """_cmd_cleanup pings, deletes the test deck, and prints a confirmation line."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("cleanup")

    mock_gw = MagicMock()
    mock_gw.ping.return_value = "6"

    with patch(_RUNNER_GATEWAY, return_value=mock_gw):
        code = _cmd_cleanup(args)

    assert code == 0
    mock_gw.ping.assert_called_once()
    mock_gw.delete_test_deck.assert_called_once()
    mock_gw.ensure_test_deck.assert_not_called()
    captured = capsys.readouterr()
    assert "Deleted test deck" in captured.out


# ---------------------------------------------------------------------------
# --fresh-home: parser + forwarding
# ---------------------------------------------------------------------------


def test_parser_fresh_home_smoke_defaults_false():
    """Parsing 'smoke' without --fresh-home gives fresh_home=False."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["smoke"])
    assert args.fresh_home is False


def test_parser_fresh_home_smoke_sets_true():
    """Parsing 'smoke --fresh-home' gives fresh_home=True."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["smoke", "--fresh-home"])
    assert args.fresh_home is True


def test_parser_fresh_home_soak_defaults_false():
    """Parsing 'soak' without --fresh-home gives fresh_home=False."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["soak"])
    assert args.fresh_home is False


def test_parser_fresh_home_soak_sets_true():
    """Parsing 'soak --fresh-home' gives fresh_home=True."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["soak", "--fresh-home"])
    assert args.fresh_home is True


def test_parser_inject_cancel_soak_defaults_none():
    """Parsing 'soak' without --inject-cancel gives inject_cancel=None."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["soak"])
    assert args.inject_cancel is None


def test_parser_inject_cancel_soak_parses_float():
    """Parsing 'soak --inject-cancel 0.5' gives inject_cancel=0.5 (float)."""
    from tests.e2e.runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["soak", "--inject-cancel", "0.5"])
    assert args.inject_cancel == 0.5


def test_cmd_soak_forwards_inject_cancel_to_inprocess(tmp_path, monkeypatch):
    """_cmd_soak passes inject_cancel through to run_inprocess_soak."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", inject_cancel=0.25)
    mock_report = _fake_soak_report()

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, return_value=mock_report) as mock_run,
    ):
        _cmd_soak(args)

    assert mock_run.call_args.kwargs.get("inject_cancel") == 0.25


def test_cmd_soak_inject_cancel_rejected_for_crossprocess(tmp_path, monkeypatch, capsys):
    """--inject-cancel with --mode crossprocess is a clean ERROR (exit 2), no run."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", mode="crossprocess", inject_cancel=0.1)

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_CROSSPROCESS) as mock_cross,
    ):
        code = _cmd_soak(args)

    assert code == 2
    assert "inprocess" in capsys.readouterr().err
    mock_cross.assert_not_called()


def test_cmd_soak_forwards_full_window_to_inprocess(tmp_path, monkeypatch):
    """_cmd_soak passes full_window through to run_inprocess_soak."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", full_window=True)
    mock_report = _fake_soak_report()

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, return_value=mock_report) as mock_run,
    ):
        _cmd_soak(args)

    assert mock_run.call_args.kwargs.get("full_window") is True


def test_cmd_soak_full_window_rejected_for_crossprocess(tmp_path, monkeypatch, capsys):
    """--full-window with --mode crossprocess is a clean ERROR (exit 2), no run."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", mode="crossprocess", full_window=True)

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_CROSSPROCESS) as mock_cross,
    ):
        code = _cmd_soak(args)

    assert code == 2
    assert "inprocess" in capsys.readouterr().err
    mock_cross.assert_not_called()


def test_parser_full_window_soak_defaults_false():
    """Parsing 'soak' without --full-window gives full_window=False."""
    from tests.e2e.runner import _build_parser

    args = _build_parser().parse_args(["soak"])
    assert args.full_window is False


def test_parser_full_window_soak_sets_true():
    """Parsing 'soak --full-window' gives full_window=True."""
    from tests.e2e.runner import _build_parser

    args = _build_parser().parse_args(["soak", "--full-window"])
    assert args.full_window is True


def test_cmd_smoke_forwards_fresh_home_true(tmp_path, monkeypatch):
    """_cmd_smoke passes fresh_home=True to run_inprocess_soak when flag set."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("smoke", fresh_home=True)
    mock_report = _fake_soak_report()

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, return_value=mock_report) as mock_run,
    ):
        _cmd_smoke(args)

    _kw = mock_run.call_args.kwargs
    assert _kw.get("fresh_home") is True


def test_cmd_smoke_forwards_fresh_home_false_by_default(tmp_path, monkeypatch):
    """_cmd_smoke passes fresh_home=False when flag is absent."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("smoke")
    mock_report = _fake_soak_report()

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, return_value=mock_report) as mock_run,
    ):
        _cmd_smoke(args)

    _kw = mock_run.call_args.kwargs
    assert _kw.get("fresh_home") is False


def test_cmd_soak_forwards_fresh_home_true(tmp_path, monkeypatch):
    """_cmd_soak passes fresh_home=True to the runner when flag set."""
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", str(tmp_path / "e2e_home"))
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)

    args = _args("soak", fresh_home=True)
    mock_report = _fake_soak_report()

    with (
        patch(_RUNNER_SET_HOME),
        patch(_RUNNER_RUNDIR),
        patch(_RUNNER_INPROCESS, return_value=mock_report) as mock_run,
    ):
        _cmd_soak(args)

    _kw = mock_run.call_args.kwargs
    assert _kw.get("fresh_home") is True

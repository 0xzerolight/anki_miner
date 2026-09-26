"""``--api``: the two-call mining API for other programs (API.md).

Each call writes exactly one JSON verdict line to fd 1 and exits 0; any other
exit is a crash. ``prepare`` and ``commit`` hold the instance lock; ``check``,
``version``, ``profiles`` and ``settings-export`` run at any time. The log goes
to ``anki_miner.api.log`` (installed by ``cli.entry`` before this runs).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from anki_miner import __version__
from anki_miner.cli.api.contract import API_SCHEMA, BAD_ARGUMENTS, COMMANDS, INTERNAL, ApiError
from anki_miner.cli.entry import _prepare_process, _private_stdout

logger = logging.getLogger(__name__)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    """The ``--api`` grammar (API.md, "Calling it")."""
    parser = _Parser(prog="AnkiMiner --api")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("prepare").add_argument("run_file", type=Path)
    commands.add_parser("commit").add_argument("commit_file", type=Path)
    check = commands.add_parser("check")
    check.add_argument("--profile")
    check.add_argument("--language", required=True)
    commands.add_parser("version")
    commands.add_parser("profiles")
    export = commands.add_parser("settings-export")
    export.add_argument("--profile")
    export.add_argument("--language", required=True)
    export.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Sequence[str]) -> int:
    """Run one API command; its verdict is the only line on stdout."""
    with _private_stdout() as write:
        verdict = _verdict(list(argv))
        write((json.dumps(verdict, ensure_ascii=True) + "\n").encode("ascii"))
    return 0


def _verdict(argv: list[str]) -> dict[str, object]:
    command = argv[0] if argv and argv[0] in COMMANDS else None
    try:
        args = build_parser().parse_args(argv)
    except _UsageError as exc:
        return _failed(command, ApiError(BAD_ARGUMENTS, str(exc)))
    except SystemExit:  # --help: printed to (redirected) stdout, i.e. stderr
        return _ok(command)
    try:
        _prepare_process()
        return _dispatch(args)
    except ApiError as exc:
        return _failed(args.command, exc)
    except Exception as exc:  # noqa: BLE001 — the caller always gets a verdict, never a traceback
        logger.exception("API %s failed", args.command)
        return _failed(args.command, ApiError(INTERNAL, f"{type(exc).__name__}: {exc}"))


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    from anki_miner.cli.api import commands

    if args.command == "version":
        return _ok(
            "version", result={"schema": API_SCHEMA, "app": __version__, "commands": list(COMMANDS), "features": []}
        )
    if args.command == "profiles":
        return _ok("profiles", result=commands.profiles_result())
    if args.command == "check":
        return _ok("check", result=commands.check_result(args.profile, args.language))
    if args.command == "settings-export":
        commands.settings_export(args.profile, args.language, args.out)
        return _ok("settings-export")
    raise ApiError(BAD_ARGUMENTS, f"Not available yet: {args.command}")


def _ok(command: str | None, **fields: object) -> dict[str, object]:
    return {"schema": API_SCHEMA, "command": command, "ok": True, "error": None, "message": None, **fields}


def _failed(command: str | None, error: ApiError) -> dict[str, object]:
    return {
        "schema": API_SCHEMA,
        "command": command,
        "ok": False,
        "error": error.code,
        "message": error.message,
        "runs": [],
    }

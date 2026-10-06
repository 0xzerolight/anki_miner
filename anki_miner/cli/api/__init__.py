"""``--api``: the mining API for other programs (API.md).

Each call writes exactly one JSON verdict line to fd 1 and exits 0; any other
exit is a crash. ``mine`` (but not a dry run), ``settings-import`` and ``setup``
hold the instance lock; ``render``, ``media``, ``check``, ``version``, ``profiles`` and
``settings-export`` run at any time. The log goes to ``anki_miner.api.log``
(installed by ``cli.entry`` before this runs).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NoReturn

from anki_miner import __version__
from anki_miner.cli.api.contract import (
    API_SCHEMA,
    BAD_ARGUMENTS,
    BAD_RUN_FILE,
    BUSY,
    CANCELLED,
    COMMANDS,
    FEATURES,
    INTERNAL,
    ApiError,
)
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
    commands.add_parser("mine").add_argument("run_file", type=Path)
    check = commands.add_parser("check")
    check.add_argument("--profile")
    check.add_argument("--language", required=True)
    commands.add_parser("version")
    commands.add_parser("profiles")
    export = commands.add_parser("settings-export")
    export.add_argument("--profile")
    export.add_argument("--language", required=True)
    export.add_argument("--out", type=Path, required=True)
    imported = commands.add_parser("settings-import")
    imported.add_argument("file", type=Path)
    imported.add_argument("--language", required=True)
    target = imported.add_mutually_exclusive_group(required=True)
    target.add_argument("--name")
    target.add_argument("--profile")
    commands.add_parser("render").add_argument("run_file", type=Path)
    commands.add_parser("media").add_argument("media_file", type=Path)
    setup = commands.add_parser("setup")
    setup.add_argument("--language", required=True)
    setup.add_argument("--progress", type=Path, required=True)
    setup.add_argument("--profile")
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
            "version",
            result={"schema": API_SCHEMA, "app": __version__, "commands": list(COMMANDS), "features": list(FEATURES)},
        )
    if args.command == "profiles":
        return _ok("profiles", result=commands.profiles_result())
    if args.command == "check":
        return _ok("check", result=commands.check_result(args.profile, args.language))
    if args.command == "settings-export":
        commands.settings_export(args.profile, args.language, args.out)
        return _ok("settings-export")
    if args.command == "render":
        return _render(args)
    if args.command == "media":
        from anki_miner.cli.api import files, media

        verdicts = media.media_runs(files.parse_media_file(files.read_json_file(args.media_file)))
        return {**_ok("media", runs=verdicts), "ok": all(v["ok"] for v in verdicts)}
    if args.command == "settings-import":
        from anki_miner.cli.api import settings_write

        with _locked():
            result = settings_write.import_settings(args.file, args.language, name=args.name, profile_id=args.profile)
        return _ok("settings-import", result=result)
    if args.command == "setup":
        return _setup(args)
    return _run(args)


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """The run lock for one call; BUSY when a window or another run holds it (acquire_run_lock says which)."""
    from anki_miner.cli.entry import Busy, acquire_run_lock

    try:
        lock = acquire_run_lock()
    except Busy as exc:
        raise ApiError(BUSY, str(exc)) from exc
    try:
        yield
    finally:
        lock.unlock()


def _run(args: argparse.Namespace) -> dict[str, object]:
    """mine: the run file checked first, then the runs; a real mine under the run lock, a dry run without."""
    from anki_miner.cli.api import files, runs
    from anki_miner.cli.entry import _cancel_on_signals

    job = files.parse_run_file(files.read_json_file(args.run_file))
    cancel = threading.Event()
    with contextlib.ExitStack() as stack:
        # A dry run writes nothing shared (no Anki, known-words or stats DB): it runs beside the window and other runs.
        if not job.dry_run:
            stack.enter_context(_locked())
        stack.enter_context(_cancel_on_signals(cancel))
        verdicts = runs.mine_runs(job, cancel, runs.Kind.DRY_RUN) if job.dry_run else runs.mine_runs(job, cancel)
    # With several runs the call is ok only if every run is; each run carries its own error.
    return {**_ok(args.command, runs=verdicts), "ok": all(v["ok"] for v in verdicts)}


def _render(args: argparse.Namespace) -> dict[str, object]:
    """render: the run file as for mine, no lock (nothing reaches Anki)."""
    from anki_miner.cli.api import files, runs
    from anki_miner.cli.entry import _cancel_on_signals

    job = files.parse_run_file(files.read_json_file(args.run_file))
    if job.dry_run:
        raise ApiError(BAD_RUN_FILE, "dry_run is for mine; render writes nothing to Anki already.")
    cancel = threading.Event()
    with _cancel_on_signals(cancel):
        verdicts = runs.mine_runs(job, cancel, runs.Kind.RENDER)
    return {**_ok(args.command, runs=verdicts), "ok": all(v["ok"] for v in verdicts)}


def _setup(args: argparse.Namespace) -> dict[str, object]:
    """setup: under the run lock; SIGINT/SIGTERM stop it after the item in flight."""
    from anki_miner.cli.api import setup
    from anki_miner.cli.entry import _cancel_on_signals

    cancel = threading.Event()
    with _locked(), _cancel_on_signals(cancel):
        outcome = setup.run_setup(args.language, args.profile, args.progress, cancel)
    verdict = _ok(args.command, result=outcome.result)
    if outcome.cancelled:
        return {**verdict, "ok": False, "error": CANCELLED, "message": "Setup was cancelled."}
    return {**verdict, "ok": not outcome.failed}


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

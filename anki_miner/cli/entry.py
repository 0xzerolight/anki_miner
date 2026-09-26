"""``anki-miner mine ...``: the command-line entry point for other tools.

Reached from the bundle's only entry script (``gui/launch.py`` dispatches
``COMMANDS`` before any GUI bootstrap) and from the pip ``anki-miner`` console
script. The output contract is in CLI.md; ``events.py`` owns the wire format.

The single-instance lock is the GUI's own (``instance.lock``): two processes
writing the rollback-journal known-words and stats databases lose writes, so a
run refuses with ``busy`` instead of waiting or proceeding. Everything that
writes to the user's home — config migration, the log — happens only after the
lock is held.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import IO, TYPE_CHECKING, NoReturn

from anki_miner import __version__
from anki_miner.cli import runner
from anki_miner.cli.events import SCHEMA_VERSION, EventSink, fd_writer
from anki_miner.cli.runner import MiningRun
from anki_miner.config import paths as config_paths
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.utils.logging_ext import suppressed

if TYPE_CHECKING:
    from PyQt6.QtCore import QLockFile

logger = logging.getLogger(__name__)

#: First-argument words that select the CLI. Mirrored as a literal in
#: ``gui/launch.py`` (which must not import this package at boot); a test pins
#: the two equal.
COMMANDS = frozenset({"mine", "version"})

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_BUSY = 3
EXIT_SETUP = 4
EXIT_CANCELLED = 130

_EXIT_BY_STATUS = {
    "success": EXIT_OK,
    "partial": EXIT_FAILED,
    "failed": EXIT_FAILED,
    "error": EXIT_FAILED,
    "usage_error": EXIT_USAGE,
    "busy": EXIT_BUSY,
    "setup_error": EXIT_SETUP,
    "cancelled": EXIT_CANCELLED,
}

_WINDOW_OPEN_MESSAGE = "The Anki Miner window is open. Close it, then try again."
_OTHER_RUN_MESSAGE = "Another Anki Miner command-line or API run is working. Wait for it to finish, then try again."
_NOT_SET_UP_MESSAGE = "Anki Miner has no saved settings yet. Open Anki Miner once and finish setup, then try again."


class _UsageError(Exception):
    pass


class Busy(Exception):
    """Another Anki Miner process is using the user's home; the message says which."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    """The ``anki-miner`` argument grammar (see CLI.md)."""
    parser = _Parser(prog="anki-miner", description="Mine vocabulary into Anki with your Anki Miner settings.")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("version", help="Print the app and output-schema versions as JSON.")
    mine = commands.add_parser("mine", help="Mine cards; prints JSON Lines (see CLI.md).")
    sources = mine.add_subparsers(dest="source", required=True, parser_class=_Parser)

    def add_deck(p: argparse.ArgumentParser) -> None:
        p.add_argument("--deck", help="Anki deck to add cards to (default: the deck in your settings).")

    batch = sources.add_parser("batch", help="Pair videos and subtitles in two folders by episode number.")
    batch.add_argument("video_dir", type=Path)
    batch.add_argument("subtitle_dir", type=Path)
    add_deck(batch)
    pairs = sources.add_parser("pairs", help="Mine explicit video/subtitle pairs.")
    pairs.add_argument("--pair", nargs=2, action="append", required=True, type=Path, metavar=("VIDEO", "SUBTITLE"))
    add_deck(pairs)
    reading = sources.add_parser("reading", help="Mine subtitle files without video (also novels, mokuro manga).")
    reading.add_argument("paths", nargs="+", type=Path)
    add_deck(reading)
    youtube = sources.add_parser("youtube", help="Download and mine YouTube videos.")
    youtube.add_argument("urls", nargs="+")
    add_deck(youtube)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command; the last stdout line is always its ``result`` event."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    with _private_stdout() as write:
        sink = EventSink(write=write)
        try:
            return _dispatch(args_list, sink)
        except Exception as exc:  # noqa: BLE001 — the caller always gets a result line, never a traceback
            logger.exception("CLI failed")
            return _finish(sink, "error", f"{type(exc).__name__}: {exc}")


def _dispatch(args_list: list[str], sink: EventSink) -> int:
    try:
        args = build_parser().parse_args(args_list)
    except _UsageError as exc:
        return _finish(sink, "usage_error", str(exc))
    except SystemExit as exc:
        # --help printed to (redirected) stdout, i.e. stderr, and asked to exit.
        if exc.code in (0, None):
            return _finish(sink, "success")
        raise
    if args.command == "version":
        return _finish(sink, "success", app_version=__version__)
    return _mine(args, sink)


def _mine(args: argparse.Namespace, sink: EventSink) -> int:
    try:
        jobs = _jobs(args)
    except runner.InputError as exc:
        return _finish(sink, "usage_error", str(exc))

    _prepare_process()
    try:
        lock = acquire_run_lock()
    except Busy as exc:
        return _finish(sink, "busy", str(exc))
    try:
        if not _settings_exist():
            return _finish(sink, "setup_error", _NOT_SET_UP_MESSAGE)
        config = GUIConfigManager.load_config()
        _start_log(config.log_path)
        if args.deck:
            config = replace(config, anki_deck_name=args.deck)
        cancel = threading.Event()
        sink.emit("start", schema=SCHEMA_VERSION, app_version=__version__, command=args.source, items=len(jobs))
        logger.info("CLI run start: command=%s items=%d", args.source, len(jobs))
        with _cancel_on_signals(cancel):
            try:
                reports = MiningRun(config, sink, cancel).run(jobs)
            except runner.SetupFailure as exc:
                return _finish(sink, "setup_error", str(exc))
        status = runner.run_status(reports, cancelled=cancel.is_set())
        logger.info("CLI run end: status=%s", status)
        return _finish(
            sink,
            status,
            cards_created=sum(r.cards_created for r in reports),
            items=[r.to_json() for r in reports],
        )
    finally:
        lock.unlock()


def _jobs(args: argparse.Namespace) -> list[runner.Job]:
    if args.source == "batch":
        return list(runner.batch_jobs(args.video_dir, args.subtitle_dir))
    if args.source == "pairs":
        return list(runner.pair_jobs([(video, subtitle) for video, subtitle in args.pair]))
    if args.source == "reading":
        return list(runner.reading_jobs(args.paths))
    return list(runner.youtube_jobs(args.urls))


def _finish(
    sink: EventSink,
    status: str,
    error: str | None = None,
    *,
    cards_created: int = 0,
    items: list[dict[str, object]] | None = None,
    **fields: object,
) -> int:
    """Emit the one ``result`` line; every result carries the same core keys."""
    sink.emit(
        "result",
        schema=SCHEMA_VERSION,
        status=status,
        error=error,
        cards_created=cards_created,
        items=items if items is not None else [],
        **fields,
    )
    return _EXIT_BY_STATUS[status]


@contextlib.contextmanager
def _private_stdout() -> Iterator[Callable[[bytes], None]]:
    """Give the JSON stream a private copy of fd 1 and send everything else to stderr.

    A dependency's ``print()``, a native library writing fd 1 and (on POSIX) a
    child process inheriting stdout would each corrupt the stream the calling
    tool parses. During the run fd 1 and ``sys.stdout`` point at stderr (devnull
    when there is none — a console=False Windows exe launched without one);
    events go to the saved descriptor. On Windows a child spawned without an
    explicit stdout still inherits the original handle, but every spawn site in
    the app captures stdout. Both are restored on exit.
    """
    # Text already buffered in the original stdout objects (an import-time print
    # while stdout is a block-buffered pipe, or a library holding a cached
    # reference) would otherwise be flushed at interpreter exit — after fd 1 is
    # restored, i.e. after the result line. Flush them while fd 1 is stderr.
    originals = [s for s in {id(s): s for s in (sys.stdout, sys.__stdout__)}.values() if s is not None]

    def flush_originals() -> None:
        for stream in originals:
            with suppressed(logger, "CLI stdout flush"):
                stream.flush()

    flush_originals()  # before the swap: earlier output stays where it was going
    try:
        event_fd: int | None = _private_copy(1)
    except OSError:  # no stdout at all: nowhere for events; the exit code still reports
        event_fd = None
    stderr_ok = _writable(2)
    if event_fd is not None:
        if stderr_ok:
            os.dup2(2, 1)
        else:
            null_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null_fd, 1)
            os.close(null_fd)
    python_target: IO[str] | None = sys.stderr if stderr_ok else None
    devnull: IO[str] | None = None
    if python_target is None:
        devnull = python_target = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 — closed below
    try:
        with contextlib.redirect_stdout(python_target):
            yield fd_writer(event_fd) if event_fd is not None else _discard
    finally:
        flush_originals()  # while fd 1 still points at stderr
        if event_fd is not None:
            os.dup2(event_fd, 1)
            os.close(event_fd)
        if devnull is not None:
            devnull.close()


def _private_copy(fd: int) -> int:
    """Duplicate *fd* onto a descriptor above the standard three.

    A plain ``os.dup`` takes the lowest free number, which is 2 when the caller
    closed stderr (``2>&-``, a daemon) — and the "stderr" redirect below would
    then aim straight back at the event stream. Non-inheritable either way.
    """
    if sys.platform == "win32":
        return os.dup(fd)
    import fcntl

    return fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)


def _writable(fd: int) -> bool:
    """Whether *fd* is open for writing.

    Open is not enough: with stderr closed at launch, fd 2 is free for the next
    file the process opens during its imports — libffi, for one, keeps its own
    shared object open read-only there for its trampolines.
    """
    try:
        if sys.platform == "win32":
            os.fstat(fd)
            return True
        import fcntl

        return (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE) in (os.O_WRONLY, os.O_RDWR)
    except OSError:
        return False


def _discard(_data: bytes) -> None:
    """Event writer for a process started with no stdout."""


@contextlib.contextmanager
def _cancel_on_signals(cancel: threading.Event) -> Iterator[None]:
    """SIGINT/SIGTERM set the run's cancel event; the processor stops at its next checkpoint."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signums = [getattr(signal, name) for name in ("SIGINT", "SIGTERM") if hasattr(signal, name)]
    previous = {signum: signal.getsignal(signum) for signum in signums}

    def handler(signum: int, _frame: object) -> None:
        logger.warning("CLI received signal %d; cancelling after the current step", signum)
        cancel.set()

    for signum in signums:
        signal.signal(signum, handler)
    try:
        yield
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old)


def _prepare_process() -> None:
    """The GUI boot's pre-Qt process steps a mining run also needs (no file writes)."""
    from anki_miner.gui import app as gui_app
    from anki_miner.gui import launch
    from anki_miner.services.asr.asr_pack_installer import ensure_asr_pack_on_syspath
    from anki_miner.services.language_pack_installer import ensure_language_packs_on_syspath

    launch._create_windows_app_mutex()
    launch._inject_system_truststore()
    gui_app._scrub_pyinstaller_env()
    ensure_language_packs_on_syspath()
    ensure_asr_pack_on_syspath()


def acquire_run_lock() -> QLockFile:
    """The GUI's single-instance lock, refused while any window or another run is open.

    Every window holds its own marker (gui/app.py WINDOW_MARKER_PREFIX), so a
    live marker means a window is open even when it runs without instance.lock;
    with none live, a held instance.lock can only be another run. A dead
    process's marker is reclaimed by tryLock and removed.
    """
    from anki_miner.gui.app import WINDOW_MARKER_PREFIX, _acquire_instance_lock

    home = config_paths.ANKI_MINER_HOME
    home.mkdir(parents=True, exist_ok=True)  # QLockFile cannot lock inside a missing directory
    # Probe every marker (no short-circuit), so each stale one is cleared on the way.
    live_windows = [path for path in home.glob(f"{WINDOW_MARKER_PREFIX}*.lock") if _held(path)]
    if live_windows:
        raise Busy(_WINDOW_OPEN_MESSAGE)
    lock, _proceed = _acquire_instance_lock(home / "instance.lock", lambda: False)
    if lock is None:
        raise Busy(_OTHER_RUN_MESSAGE)
    return lock


def _held(path: Path) -> bool:
    """Whether a live process holds the lock file *path*; a dead one's file is removed."""
    from PyQt6.QtCore import QLockFile

    probe = QLockFile(str(path))
    if probe.tryLock(0):
        probe.unlock()  # removes the file
        return False
    return True


def _settings_exist() -> bool:
    """Whether Anki Miner was ever set up (load_config silently falls back to defaults)."""
    config_file = GUIConfigManager.CONFIG_FILE
    return config_file.exists() or config_file.with_name(config_file.name + ".bak").exists()


def _start_log(log_path: Path) -> None:
    """Attach the normal log sink — only once the lock is held — with the GUI's session markers."""
    from anki_miner.gui import app as gui_app

    try:
        gui_app._configure_logging(log_path)
        gui_app._log_session_boundary()
        atexit.register(gui_app._log_session_end, None, reason="atexit")
    except Exception:  # noqa: BLE001 — logging trouble must not stop a mining run
        logger.exception("CLI could not configure the log file")

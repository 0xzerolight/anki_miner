"""CLI launcher for the E2E GUI test harness.

Three subcommands wrap the soak building blocks (``tests/e2e/soak.py``) and the
Anki gateway for an agent / CI caller:

* ``smoke`` — one real mining session + screenshots (process mode, needs Anki).
* ``soak`` — multi-session soak (in-process or cross-process), preview or real,
  faithful or ``--bypass-known-words``.
* ``cleanup`` — delete a leftover test deck after inspecting a failure.

Machine-readable contract: after a run the runner PRINTS ``RUN_DIR=<abs>`` and
``REPORT=<abs>`` to stdout plus the divergence verdict, so the caller can locate
artifacts. EXIT CODE is 0 on PASS/WARN, non-zero on FAIL. An expected
"Anki down" case (smoke / non-preview soak) exits non-zero with a one-line
``ERROR:`` message and NO traceback (caught :class:`AnkiUnreachableError`).
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from tests._home_isolation import set_test_home
from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError, ForeignDeckError
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.soak import (
    SoakReport,
    run_crossprocess_soak,
    run_inprocess_soak,
)

__all__ = ["main"]

# Holds the QApplication the CLI creates for in-process Qt-driving commands so
# its Python wrapper is not garbage-collected mid-run (which would tear Qt down).
_QAPP = None


def _anki_down(e2e: E2EConfig) -> int:
    """Print the one-line Anki-unreachable error to stderr and return exit code 2."""
    print(
        f"ERROR: Anki not reachable at {e2e.ankiconnect_url} — start Anki with AnkiConnect",
        file=sys.stderr,
    )
    return 2


def _foreign_deck(e2e: E2EConfig) -> int:
    """Print the one-line foreign-deck error to stderr and return exit code 2."""
    print(
        f"ERROR: Test deck {e2e.deck_name!r} already has cards from a prior run. "
        f"Run `python scripts/run_e2e.py cleanup` to delete it, then retry.",
        file=sys.stderr,
    )
    return 2


def _build_config(args: argparse.Namespace) -> E2EConfig:
    """Build ``E2EConfig.from_env()`` applying any trivial CLI overrides."""
    e2e = E2EConfig.from_env()
    overrides: dict = {}
    if getattr(args, "home", None):
        overrides["test_home"] = args.home
    if getattr(args, "deck", None):
        overrides["deck_name"] = args.deck
    if getattr(args, "ankiconnect_url", None):
        overrides["ankiconnect_url"] = args.ankiconnect_url
    if getattr(args, "timeout", None) is not None:
        # Override both timeout fields together: a slow first faithful run can
        # spuriously time out at the default 120/300 s; --timeout lets the
        # operator raise both caps in one flag without editing env vars.
        overrides["result_timeout_s"] = args.timeout
        overrides["session_timeout_s"] = args.timeout
    if overrides:
        # runs_root tracks test_home via __post_init__ unless pinned; clear it so
        # an overridden home re-derives its runs_root.
        if "test_home" in overrides:
            overrides["runs_root"] = None
        e2e = dataclasses.replace(e2e, **overrides)
    return e2e


def _emit(soak: SoakReport, run_dir: RunDir) -> int:
    """Print the machine-readable lines + verdict; return the process exit code."""
    report_path = run_dir.path / "report.json"
    print(f"RUN_DIR={run_dir.path}")
    print(f"REPORT={report_path}")
    divergence_verdict = soak.divergence.get("verdict", "?")
    print(f"VERDICT={soak.verdict} (divergence={divergence_verdict})")
    # PASS/WARN -> 0; anything else (FAIL) -> non-zero.
    return 0 if soak.verdict in ("PASS", "WARN") else 1


def _cmd_smoke(args: argparse.Namespace) -> int:
    """One real mining session + screenshots (process mode, needs Anki)."""
    e2e = _build_config(args)
    # Belt-and-suspenders: re-assert ANKI_MINER_HOME + patch any home snapshot a
    # module imported transitively before the shim's pre-import env-set took hold
    # (the standalone runner never sees conftest's isolation fixtures).
    set_test_home(e2e.test_home)
    run_dir = RunDir(e2e.runs_root, label="smoke")
    try:
        soak = run_inprocess_soak(
            e2e,
            sessions=1,
            preview=False,
            bypass_known_words=True,
            run_dir=run_dir,
            test_home=e2e.test_home,
            fresh_home=getattr(args, "fresh_home", False),
        )
    except AnkiUnreachableError:
        return _anki_down(e2e)
    except ForeignDeckError:
        return _foreign_deck(e2e)
    return _emit(soak, run_dir)


def _cmd_soak(args: argparse.Namespace) -> int:
    """Multi-session soak (in-process or cross-process; preview or real)."""
    e2e = _build_config(args)
    # See _cmd_smoke: re-assert home env + patch transitively-imported snapshots.
    set_test_home(e2e.test_home)
    try:
        if args.policy == "first_n":
            e2e = dataclasses.replace(e2e, curation_policy="first_n", first_n=args.first_n)
        elif args.policy != "all":
            e2e = dataclasses.replace(e2e, curation_policy=args.policy)
    except ValueError as exc:
        # E2EConfig.__post_init__ rejects e.g. `--policy first_n` without a
        # positive `--first-n`. Surface a clean one-line error, not a traceback.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    run_dir = RunDir(e2e.runs_root, label=f"soak-{args.mode}")
    runner = run_crossprocess_soak if args.mode == "crossprocess" else run_inprocess_soak
    try:
        soak = runner(
            e2e,
            sessions=args.sessions,
            preview=args.preview,
            bypass_known_words=args.bypass_known_words,
            run_dir=run_dir,
            test_home=e2e.test_home,
            fresh_home=getattr(args, "fresh_home", False),
        )
    except AnkiUnreachableError:
        return _anki_down(e2e)
    except ForeignDeckError:
        return _foreign_deck(e2e)
    return _emit(soak, run_dir)


def _cmd_cleanup(args: argparse.Namespace) -> int:
    """Delete a leftover test deck (after inspecting a failure)."""
    e2e = _build_config(args)
    try:
        gateway = AnkiGateway(e2e)
        gateway.ping()
        gateway.delete_test_deck()
    except AnkiUnreachableError:
        return _anki_down(e2e)
    print(f"Deleted test deck {e2e.deck_name!r}.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with the three subcommands."""
    parser = argparse.ArgumentParser(prog="run_e2e", description="E2E GUI test harness runner.")
    # Shared trivial overrides on the top-level parser so every subcommand has them.
    parser.add_argument("--home", help="Override the isolated test home.")
    parser.add_argument("--deck", help="Override the test deck name.")
    parser.add_argument("--ankiconnect-url", dest="ankiconnect_url", help="Override the AnkiConnect URL.")

    sub = parser.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke", help="One real mining session + screenshots (needs Anki).")
    smoke.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override both result_timeout_s and session_timeout_s (default: 120/300 s).",
    )
    smoke.add_argument(
        "--fresh-home",
        dest="fresh_home",
        action="store_true",
        help=(
            "Delete the test home's contents before running so the run starts "
            "clean (safe: refuses the real home). Pre-run baseline is always "
            "recorded in report.json regardless of this flag."
        ),
    )

    soak = sub.add_parser("soak", help="Multi-session soak (bug-hunt).")
    soak.add_argument("--mode", choices=["inprocess", "crossprocess"], default="inprocess")
    soak.add_argument("--sessions", type=int, default=5)
    soak.add_argument(
        "--preview",
        action="store_true",
        help="Preview only (parse+filter, no Anki). Default: real card creation.",
    )
    soak.add_argument(
        "--bypass-known-words",
        dest="bypass_known_words",
        action="store_true",
        help="Deterministic card-everything mode. Default: faithful (the bug-hunt mode).",
    )
    soak.add_argument("--policy", choices=["all", "first_n", "none"], default="all")
    soak.add_argument("--first-n", dest="first_n", type=int, default=0)
    soak.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override both result_timeout_s and session_timeout_s (default: 120/300 s).",
    )
    soak.add_argument(
        "--fresh-home",
        dest="fresh_home",
        action="store_true",
        help=(
            "Delete the test home's contents before running so the run starts "
            "clean (safe: refuses the real home). Pre-run baseline is always "
            "recorded in report.json regardless of this flag."
        ),
    )

    sub.add_parser("cleanup", help="Delete a leftover test deck.")

    return parser


def _ensure_qapplication() -> None:
    """Ensure a QApplication exists for the in-process Qt-driving commands.

    ``run_inprocess_soak`` builds a real ``EpisodeTabDriver`` (a QWidget) in THIS
    process and relies on a live QApplication (pytest supplies pytest-qt's
    ``qapp``; the CLI must supply its own). Offscreen is set by the shim, but
    default here too so a direct ``main`` call is also headless-safe.
    """
    global _QAPP
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    _QAPP = QApplication.instance() or QApplication(sys.argv[:1])


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch to the chosen subcommand. Returns the exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "smoke":
        _ensure_qapplication()
        return _cmd_smoke(args)
    if args.command == "soak":
        _ensure_qapplication()
        return _cmd_soak(args)
    if args.command == "cleanup":
        return _cmd_cleanup(args)
    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - argparse exits first


if __name__ == "__main__":
    raise SystemExit(main())

"""Standard-library bootstrap for the GUI application."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from types import TracebackType
from typing import cast

TRUSTSTORE_INJECTED = False
_EARLY_EXCEPTHOOK_INSTALLED = False
APP_MUTEX_NAME = r"Local\AnkiMiner-15B09250-AC39-4792-A15A-B73BD8E218A1"
_APP_MUTEX_HANDLE: int | None = None

# The supervised ffsubsync child re-enters this same entry script (ffsubsync
# ships no __main__, and a frozen bundle has no interpreter to run one with),
# so the flag is answered here rather than in __main__.py: this module is what
# PyInstaller runs. Duplicated as a literal instead of imported, because
# importing the child module would drag the whole services package — Qt
# included — into every application boot; a test pins the two equal.
FFSUBSYNC_CHILD_FLAG = "--ffsubsync-child"

_CA_ENV_VARS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")
# Module + line number form an exact source coordinate against the version in
# the later session header. Thread name is intentionally absent: bare Qt worker
# names are unhelpful ``Dummy-N`` noise and consume materially more ring space.
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s:%(lineno)d: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _default_anki_miner_home() -> Path:
    try:
        return Path.home() / ".anki_miner"
    except Exception:
        return Path(tempfile.gettempdir()) / ".anki_miner"


def get_effective_log_path(fallback: Path) -> Path:
    """Return the path owned by the active Anki Miner sink, or *fallback*."""
    for handler in reversed(logging.getLogger().handlers):
        if not getattr(handler, "_anki_miner_sink", False):
            continue
        base_filename = getattr(handler, "baseFilename", None)
        if base_filename is not None:
            return Path(base_filename)
    return Path(fallback)


def _make_early_handler(log_path: Path) -> logging.FileHandler:
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(log_path, flags, 0o600)
    try:
        handler = logging.FileHandler(
            log_path,
            mode="a",
            encoding="utf-8",
            delay=True,
            errors="backslashreplace",
        )
        handler.stream = os.fdopen(fd, "a", encoding="utf-8", errors="backslashreplace")
    except Exception:
        os.close(fd)
        raise
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    handler._anki_miner_sink = True  # type: ignore[attr-defined]
    return handler


def _install_early_crash_sink() -> None:
    global _EARLY_EXCEPTHOOK_INSTALLED

    root = logging.getLogger()
    root.setLevel(logging.WARNING)

    sink = next(
        (handler for handler in root.handlers if getattr(handler, "_anki_miner_sink", False)),
        None,
    )
    if sink is None:
        requested_path: Path | None = None
        setup_error: BaseException | None = None
        try:
            home_value = os.environ.get("ANKI_MINER_HOME")
            home = Path(home_value) if home_value else _default_anki_miner_home()
            requested_path = home / "anki_miner.log"
            home.mkdir(parents=True, exist_ok=True)
            sink = _make_early_handler(requested_path)
        except Exception as exc:
            setup_error = exc
            fallback_path = Path(tempfile.gettempdir()) / "AnkiMiner-early-crash.log"
            try:
                sink = _make_early_handler(fallback_path)
            except Exception:
                return

        root.addHandler(sink)
        if setup_error is not None:
            logging.getLogger(__name__).warning(
                "Failed to open early log at %s; using temporary fallback",
                requested_path,
                exc_info=(type(setup_error), setup_error, setup_error.__traceback__),
            )

    if _EARLY_EXCEPTHOOK_INSTALLED:
        return

    previous_hook = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            logging.getLogger(__name__).critical(
                "Unhandled exception during early startup",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        finally:
            previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    _EARLY_EXCEPTHOOK_INSTALLED = True


def _create_windows_app_mutex() -> None:
    global _APP_MUTEX_HANDLE
    if not (sys.platform == "win32" and getattr(sys, "frozen", False)):
        return
    try:
        import ctypes
        from ctypes import wintypes

        create_mutex = ctypes.windll.kernel32.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        handle = create_mutex(None, False, APP_MUTEX_NAME)
        if not handle:
            raise OSError("CreateMutexW returned a null handle")
        # Keep the handle referenced for process lifetime so Windows does not close the mutex.
        _APP_MUTEX_HANDLE = handle
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to create Windows app mutex; continuing startup",
            exc_info=True,
        )


def _inject_windows_truststore() -> None:
    global TRUSTSTORE_INJECTED
    TRUSTSTORE_INJECTED = False
    if not (getattr(sys, "frozen", False) and sys.platform == "win32"):
        return
    if any(name in os.environ for name in _CA_ENV_VARS):
        return
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to inject Windows trust store; continuing with default TLS verification",
            exc_info=True,
        )
        return
    TRUSTSTORE_INJECTED = True


def main() -> int:
    """Install early recovery, then hand control to the full GUI application."""
    # First, before the crash sink, the Windows app mutex and the instance
    # lock: the child is a headless one-shot worker for the application that
    # spawned it and must neither boot a second GUI nor contend for the
    # parent's single-instance guards.
    if len(sys.argv) > 1 and sys.argv[1] == FFSUBSYNC_CHILD_FLAG:
        from anki_miner.services.sync_engines._ffsubsync_child import main as ffsubsync_child_main

        return ffsubsync_child_main(sys.argv[2:])

    _install_early_crash_sink()
    _create_windows_app_mutex()
    _inject_windows_truststore()

    from anki_miner.gui.app import main as app_main

    return cast(int, app_main())


if __name__ == "__main__":
    sys.modules.setdefault("anki_miner.gui.launch", sys.modules[__name__])
    raise SystemExit(main())

"""Restart intent for settings that only take effect at boot (decision D39b).

Whole-UI zoom is restart-to-apply alongside Language: the choice is persisted
the moment it is made, and the panel offers *Restart now* / *Later* (Zoom is
the only interface-size control — a saved Text size folds into it on load).

This module is deliberately three functions and one flag — not a state machine.
The whole sequence is:

1. the panel resolves the executable it would relaunch (``resolve_relaunch_target``)
   and, only if that succeeds, records intent here and calls the ordinary
   ``window.close()``;
2. the existing shutdown runs unchanged — settings flush, worker cancellation and
   join, dictionary release, deferred close, config save;
3. ``app.exec()`` returns, ``anki_miner.gui.app`` releases the instance lock it
   has held for the process lifetime, and only then starts the replacement.
   The relaunch is normally this executable; an in-place Windows update names
   the installer instead (``request_restart(program, arguments)``), and the
   installer starts the new app.

Nothing here waits, polls or re-acquires a lock: the parent is fully dead before
the child is spawned, so there is no window in which two processes share the
sqlite stores and no second-instance prompt to suppress.

It lives in its own module (rather than in ``gui.app``) so a settings panel can
record intent without importing the application entry point, which imports the
main window, which imports the panel.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

_restart_requested = False
#: Set only by an in-place update that relaunches through something other than
#: this executable (the Windows installer). None means the ordinary relaunch.
_relaunch_override: tuple[Path, tuple[str, ...]] | None = None


def resolve_relaunch_target() -> Path | None:
    """Return the executable a restart would launch, or ``None`` if unknown.

    Reuses :meth:`ShortcutService.resolve_executable`, which already encodes the
    AppImage-before-frozen precedence and the pip/venv fallbacks. Resolving
    *before* recording intent is what keeps a failed restart harmless: the app
    stays open and the panel reports the problem inline.
    """
    from anki_miner.services.shortcut_service import ShortcutService

    try:
        return ShortcutService.resolve_executable()
    except Exception:
        logger.exception("Could not resolve the executable to relaunch")
        return None


def request_restart(program: Path | None = None, arguments: Sequence[str] = ()) -> None:
    """Record that the app should relaunch once the event loop exits.

    Args:
        program: Start this instead of the app itself. The Windows in-place
            update passes its installer, which relaunches the app when done.
        arguments: Command-line arguments for *program*.
    """
    global _restart_requested, _relaunch_override
    _restart_requested = True
    _relaunch_override = None if program is None else (program, tuple(arguments))


def clear_restart_request() -> None:
    """Forget a recorded restart intent (the close was refused or cancelled)."""
    global _restart_requested, _relaunch_override
    _restart_requested = False
    _relaunch_override = None


def restart_requested() -> bool:
    """Return True when a relaunch was requested and not since cleared."""
    return _restart_requested


def relaunch_command() -> tuple[Path, tuple[str, ...]] | None:
    """What to start for the recorded restart: the override, else this executable."""
    if _relaunch_override is not None:
        return _relaunch_override
    target = resolve_relaunch_target()
    return None if target is None else (target, ())

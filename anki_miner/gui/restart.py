"""Restart intent for settings that only take effect at boot (decision D39b).

Text size joins Language and whole-UI zoom as restart-to-apply: the choice is
persisted the moment it is made, and the panel offers *Restart now* / *Later*.

This module is deliberately three functions and one flag — not a state machine.
The whole sequence is:

1. the panel resolves the executable it would relaunch (``resolve_relaunch_target``)
   and, only if that succeeds, records intent here and calls the ordinary
   ``window.close()``;
2. the existing shutdown runs unchanged — settings flush, worker cancellation and
   join, dictionary release, deferred close, config save;
3. ``app.exec()`` returns, ``anki_miner.gui.app`` releases the instance lock it
   has held for the process lifetime, and only then starts the replacement.

Nothing here waits, polls or re-acquires a lock: the parent is fully dead before
the child is spawned, so there is no window in which two processes share the
sqlite stores and no second-instance prompt to suppress.

It lives in its own module (rather than in ``gui.app``) so a settings panel can
record intent without importing the application entry point, which imports the
main window, which imports the panel.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_restart_requested = False


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


def request_restart() -> None:
    """Record that the app should relaunch once the event loop exits."""
    global _restart_requested
    _restart_requested = True


def clear_restart_request() -> None:
    """Forget a recorded restart intent (the close was refused or cancelled)."""
    global _restart_requested
    _restart_requested = False


def restart_requested() -> bool:
    """Return True when a relaunch was requested and not since cleared."""
    return _restart_requested

"""Route the interpreter's out-of-band failure channels into the log.

Three failure classes never reach ``anki_miner.log`` on their own, and all
three are invisible in a frozen build, which has no stderr anybody can read:

* an exception escaping a ``threading.Thread`` body — Python prints it and the
  thread simply disappears, so a failed prewarm or importer thread looks like
  work that never started;
* an *unraisable* exception — a failure inside ``__del__``, a weakref callback
  or a GC hook, where there is no caller to raise at;
* ``warnings.warn`` — deprecations and resource warnings from the libraries the
  app drives, which are exactly the early signal that a dependency changed
  under us.

Installing these hooks is the whole module. Each one logs and then chains the
handler it replaced, so anything already reporting (a debugger, pytest's own
capture, the interpreter default) keeps its output; the log gains a copy
rather than taking the report away.

``install_process_log_hooks`` is idempotent and paired with
``uninstall_process_log_hooks`` so tests can install it for one case without
leaking a hook into the rest of the session.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

_INSTALLED = False
_previous_thread_hook: Any = None
_previous_unraisable_hook: Any = None


def _log_thread_exception(args: Any) -> None:
    """Log one exception that escaped a thread body, then chain the default.

    ``SystemExit`` is deliberately not reported: raising it is how a thread is
    asked to stop, and the interpreter's own hook already treats it as normal.
    """
    exc_type = args.exc_type
    if exc_type is not None and issubclass(exc_type, SystemExit):
        if _previous_thread_hook is not None:
            _previous_thread_hook(args)
        return
    thread = getattr(args, "thread", None)
    name = getattr(thread, "name", None) or threading.current_thread().name
    type_name = getattr(exc_type, "__name__", str(exc_type))
    logger.critical(
        "Unhandled exception in thread %s: %s: %s",
        name,
        type_name,
        args.exc_value,
        exc_info=(exc_type, args.exc_value, args.exc_traceback),
    )
    if _previous_thread_hook is not None:
        _previous_thread_hook(args)


def _log_unraisable(unraisable: Any) -> None:
    """Log one unraisable exception (``__del__``, GC, weakref callback).

    The offending object is included verbatim: an unraisable carries no call
    stack of its own worth reading, so the object's ``repr`` is usually the
    only thing that names which teardown path failed.
    """
    exc_type = unraisable.exc_type
    type_name = getattr(exc_type, "__name__", str(exc_type))
    subject = "<unrepresentable>"
    # A broken __repr__ must not mask the failure it belongs to. (bucket A)
    with contextlib.suppress(Exception):
        subject = repr(unraisable.object)
    logger.error(
        "Unraisable exception in %s: %s: %s%s",
        subject,
        type_name,
        unraisable.exc_value,
        f" ({unraisable.err_msg})" if getattr(unraisable, "err_msg", None) else "",
        exc_info=(exc_type, unraisable.exc_value, unraisable.exc_traceback),
    )
    if _previous_unraisable_hook is not None:
        _previous_unraisable_hook(unraisable)


def install_process_log_hooks() -> None:
    """Capture thread, unraisable and ``warnings`` failures into the log.

    Idempotent: a second call while installed is a no-op, so a re-entered
    ``main()`` (the E2E harness relaunches in-process) cannot stack hooks that
    each log the same failure again.
    """
    global _INSTALLED, _previous_thread_hook, _previous_unraisable_hook
    if _INSTALLED:
        return
    _previous_thread_hook = threading.excepthook
    _previous_unraisable_hook = sys.unraisablehook
    threading.excepthook = _log_thread_exception
    sys.unraisablehook = _log_unraisable
    # Routes every warnings.warn through the ``py.warnings`` logger at WARNING,
    # which the root file handler already owns. Root sits at WARNING by
    # default, so third-party deprecations land without turning on their DEBUG.
    logging.captureWarnings(True)
    _INSTALLED = True


def uninstall_process_log_hooks() -> None:
    """Put the replaced hooks back. Safe to call when nothing is installed."""
    global _INSTALLED, _previous_thread_hook, _previous_unraisable_hook
    if not _INSTALLED:
        return
    threading.excepthook = _previous_thread_hook if _previous_thread_hook is not None else threading.__excepthook__
    sys.unraisablehook = _previous_unraisable_hook if _previous_unraisable_hook is not None else sys.__unraisablehook__
    logging.captureWarnings(False)
    _previous_thread_hook = None
    _previous_unraisable_hook = None
    _INSTALLED = False

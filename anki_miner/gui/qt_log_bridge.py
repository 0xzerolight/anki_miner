"""Send Qt's own diagnostics to ``anki_miner.log``.

Qt writes platform-plugin, OpenGL, paint and QSS diagnostics through
``qDebug``/``qWarning``, whose default handler goes to stderr. A frozen bundle
has no stderr a user can read, so the single most useful line in a
"black window" or "app will not start" report — ``could not load the Qt
platform plugin``, ``QOpenGLWidget: Failed to create context`` — was never in
the log the user sent. This handler puts it there.

Two rules keep it safe to leave installed for the life of the process:

* the whole body runs inside ``suppressed``. The handler is called from Qt's C++
  side, on any thread, including during ``QApplication`` construction and
  teardown; an exception escaping it would abort the process it was installed to
  diagnose.
* consecutive identical messages collapse. Qt repeats one paint or GL warning
  per frame, which at 60 fps fills a 16 MiB log ring in minutes and evicts the
  session boundary. The first is logged; the run is closed by a single
  ``(repeated N more times)`` record once a different message arrives.

The records go to the ``anki_miner.qt`` logger — a child of ``anki_miner``, so
it inherits the DEBUG level the app pins, and one grep separates Qt's own
complaints from the app's.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

from anki_miner.utils.logging_ext import suppressed

#: Explicit, not ``__name__``: the records are Qt's, not this shim's, and the
#: short name is what makes them greppable next to the app's own loggers.
logger = logging.getLogger("anki_miner.qt")

_LEVELS: dict[QtMsgType, int] = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}

_INSTALLED = False
_previous_handler: Any = None

# Guards the collapse state only. Qt calls the handler from whichever thread
# produced the message, and two threads repeating the same warning must not
# interleave a half-updated counter into the log.
_repeat_lock = threading.Lock()
_last_key: tuple[int, str] | None = None
_repeat_count = 0


def _format(msg_type: QtMsgType, context: Any, message: str) -> str:
    """Render one Qt message: type, category, text, then C++ source position."""
    body = getattr(msg_type, "name", str(msg_type))
    category = getattr(context, "category", "") or ""
    if category:
        body = f"{body} [{category}]"
    body = f"{body}: {message}"
    file_name = getattr(context, "file", "") or ""
    if file_name:
        body = f"{body} ({file_name}:{getattr(context, 'line', 0)})"
    return body


def _flush_locked() -> None:
    """Emit the pending repeat receipt. Caller holds ``_repeat_lock``."""
    global _last_key, _repeat_count
    pending, count = _last_key, _repeat_count
    _last_key = None
    _repeat_count = 0
    if pending is not None and count:
        level, body = pending
        logger.log(level, "%s (repeated %d more times)", body, count)


def flush_qt_repeats() -> None:
    """Close an open repeat run now, without waiting for a different message.

    Called at uninstall, and by tests that need the receipt deterministically
    rather than as a side effect of the next unrelated Qt warning.
    """
    with _repeat_lock:
        _flush_locked()


def _handler(msg_type: QtMsgType, context: Any, message: str | None) -> None:
    global _last_key, _repeat_count
    with suppressed(logger, "qt message handler"):
        level = _LEVELS.get(msg_type, logging.WARNING)
        body = _format(msg_type, context, message or "")
        key = (level, body)
        with _repeat_lock:
            if key == _last_key:
                _repeat_count += 1
                return
            _flush_locked()
            _last_key = key
        logger.log(level, "%s", body)


def install_qt_message_handler() -> None:
    """Route Qt's message stream to ``anki_miner.qt``.

    Install before ``QApplication`` is constructed: platform-plugin and GL
    failures are emitted *during* construction, which is precisely the case
    this exists for. Idempotent, so a re-entered ``main()`` does not chain the
    handler onto itself.
    """
    global _INSTALLED, _previous_handler
    if _INSTALLED:
        return
    _previous_handler = qInstallMessageHandler(_handler)
    _INSTALLED = True


def uninstall_qt_message_handler() -> None:
    """Restore the handler Qt had before. Safe when nothing is installed."""
    global _INSTALLED, _previous_handler
    if not _INSTALLED:
        return
    flush_qt_repeats()
    qInstallMessageHandler(_previous_handler)
    _previous_handler = None
    _INSTALLED = False

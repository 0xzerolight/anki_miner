"""Shared helpers for compact, parseable operational log records.

Once-per-operation summaries need one spelling everywhere: a stable event
anchor followed by ordered ``key=value`` fields. Hand-built format strings let
the placeholders and positional arguments drift apart, and they too easily
leak full paths. ``log_summary`` centralizes rendering, including the basename-
only rule for paths at INFO and above.

``suppressed`` is the visible replacement for exception suppression when a
documented fallback or degraded result still needs a diagnostic. It records
the erased exception type and message without claiming ownership of a
traceback, while cooperative cancellation continues to propagate.

Summary rendering is eager. Use it only for INFO/WARNING operation receipts,
never inside a loop; hot-loop detail must be counted and summarized once.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from anki_miner.exceptions import OperationCancelled

# No module-level logger on purpose: both helpers take the caller's logger so
# records stay attributed to the module performing the operation.

_VALUE_WHITESPACE = re.compile(r"\s+")


def _render_value(value: object) -> str:
    """Render one summary value without whitespace or private path parents."""
    if value is None or value == "" or isinstance(value, (list, tuple, set, dict)) and not value:
        rendered = "-"
    elif isinstance(value, Path):
        rendered = value.name
    elif isinstance(value, (list, tuple, set)):
        rendered = ",".join(str(item) for item in value)
    else:
        rendered = str(value)
    return _VALUE_WHITESPACE.sub("_", rendered)


def log_summary(
    log: logging.Logger,
    event: str,
    /,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit one ordered ``event: key=value`` operation receipt.

    Keyword insertion order is preserved. ``None`` and empty strings or
    containers render as ``-``; non-empty lists, tuples, and sets are joined by
    commas. Paths render as ``Path.name`` because summary records are INFO or
    WARNING and must not expose absolute user paths. Runs of whitespace in any
    rendered value become one underscore.

    The body is built eagerly, so call this only once per operation at INFO or
    WARNING, never inside a loop. ``level`` is a reserved keyword-only
    parameter: callers cannot emit a summary field literally named ``level``.

    Args:
        log: Caller's module logger, preserving the record's module attribution.
        event: Stable literal grep anchor rendered before the colon.
        level: Logging level, normally ``logging.INFO`` or ``logging.WARNING``.
        **fields: Ordered summary field names and values.
    """
    # stacklevel=2 so the record's %(lineno)d resolves to the CALLER, not to
    # this helper. Without it every summary line in the app would point at this
    # one statement, defeating the line number in the log format.
    if not fields:
        log.log(level, "%s:", event, stacklevel=2)
        return

    body = " ".join(f"{key}={_render_value(value)}" for key, value in fields.items())
    log.log(level, "%s: %s", event, body, stacklevel=2)


@contextlib.contextmanager
def suppressed(
    log: logging.Logger,
    what: str,
    *,
    level: int = logging.DEBUG,
) -> Iterator[None]:
    """Log and swallow one expected ``Exception``, but preserve cancellation.

    The diagnostic deliberately omits ``exc_info``: a swallowed failure has no
    terminal traceback boundary, while a re-raised failure belongs to its
    eventual terminal handler. ``OperationCancelled`` is re-raised explicitly
    because it currently derives from ``Exception`` but represents user intent.

    Like ``timed_phase``, this helper requires the caller's logger so the record
    remains attributed to the module performing the suppressed operation. A
    module-local default here would hide the useful call-site identity.

    Args:
        log: Caller's module logger.
        what: Short description of the operation whose failure is ignored.
        level: Logging level for the diagnostic; DEBUG is the normal fallback.
    """
    try:
        yield
    except OperationCancelled:
        raise
    except Exception as exc:
        log.log(
            level,
            "Ignored failure during %s: %s: %s",
            what,
            type(exc).__name__,
            exc,
            # 3, not 2: the frame above this generator is contextlib's
            # __exit__, so only the third level reaches the `with` statement.
            stacklevel=3,
        )

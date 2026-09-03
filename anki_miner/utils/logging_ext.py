"""Shared helpers for compact, parseable operational log records.

Once-per-operation summaries need one spelling everywhere: a stable event
anchor followed by ordered ``key=value`` fields. Hand-built format strings let
the placeholders and positional arguments drift apart. ``log_summary``
centralizes rendering, including the quoting rule that keeps a value with
spaces in it a single parseable field.

Paths render verbatim, in full. A basename cannot locate the pack folder,
dictionary slot, or subtitle file an operation actually touched, and the two
candidates a user has are usually siblings. Diagnostic bundles preserve paths
by design (``diagnostics/bundle.py``), so trimming them here bought nothing and
cost the one fact that makes a report actionable.

``suppressed`` is the visible replacement for exception suppression when a
documented fallback or degraded result still needs a diagnostic. It records
the erased exception type and message, optionally with a traceback, while
cooperative cancellation continues to propagate.

``capped`` bounds a rendered list so a summary line cannot grow with the size
of the run.

Summary rendering is eager. Use it only for INFO/WARNING operation receipts,
never inside a loop; hot-loop detail must be counted and summarized once.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from anki_miner.exceptions import OperationCancelled

# No module-level logger on purpose: both helpers take the caller's logger so
# records stay attributed to the module performing the operation.

_VALUE_WHITESPACE = re.compile(r"\s")


def _render_value(value: object) -> str:
    """Render one summary value, quoting it if it contains whitespace."""
    if value is None or value == "" or isinstance(value, (list, tuple, set, dict)) and not value:
        rendered = "-"
    elif isinstance(value, Path):
        # Verbatim, not `.name`: the parent directories are the diagnosis.
        rendered = str(value)
    elif isinstance(value, (list, tuple, set)):
        rendered = ",".join(str(item) for item in value)
    else:
        rendered = str(value)
    if _VALUE_WHITESPACE.search(rendered):
        escaped = rendered.replace('"', '\\"')
        return f'"{escaped}"'
    return rendered


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
    commas. Paths render in full, verbatim: a basename cannot identify which of
    two sibling folders an operation used, and diagnostic bundles keep paths by
    design. Any rendered value containing whitespace is wrapped in double
    quotes, with embedded quotes backslash-escaped, so one field stays one
    token.

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


def capped(items: Iterable[object], limit: int = 50) -> list[str]:
    """Render ``items`` as strings, truncated to ``limit`` plus a remainder mark.

    A summary field must not grow with the size of a run: a thousand skipped
    rows would push the useful counts off the readable part of the line. The
    first ``limit`` entries are kept and the tail becomes one ``"+N more"``
    element, so the field still reports how much was elided.

    Args:
        items: Any iterable; consumed once, so generators are safe.
        limit: How many entries to render before eliding the rest.

    Returns:
        Up to ``limit`` rendered entries, plus ``"+N more"`` when truncated.
    """
    rendered: list[str] = []
    extra = 0
    for item in items:
        if len(rendered) < limit:
            rendered.append(str(item))
        else:
            extra += 1
    if extra:
        rendered.append(f"+{extra} more")
    return rendered


@contextlib.contextmanager
def suppressed(
    log: logging.Logger,
    what: str,
    *,
    level: int = logging.DEBUG,
    exc_info: bool = False,
) -> Iterator[None]:
    """Log and swallow one expected ``Exception``, but preserve cancellation.

    The diagnostic omits ``exc_info`` by default: a swallowed failure has no
    terminal traceback boundary, while a re-raised failure belongs to its
    eventual terminal handler. Pass ``exc_info=True`` where the swallowed
    failure is genuinely unexpected and the stack is the only way to find the
    culprit. ``OperationCancelled`` is re-raised explicitly because it
    currently derives from ``Exception`` but represents user intent.

    Like ``timed_phase``, this helper requires the caller's logger so the record
    remains attributed to the module performing the suppressed operation. A
    module-local default here would hide the useful call-site identity.

    Args:
        log: Caller's module logger.
        what: Short description of the operation whose failure is ignored.
        level: Logging level for the diagnostic; DEBUG is the normal fallback.
        exc_info: Attach the traceback of the swallowed exception.
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
            # `or None`, not the flag itself: logging stores a falsy exc_info
            # verbatim, so `False` would land on the record where every reader
            # (and caplog) expects `None` for "no traceback attached".
            exc_info=exc_info or None,
            # 3, not 2: the frame above this generator is contextlib's
            # __exit__, so only the third level reaches the `with` statement.
            stacklevel=3,
        )

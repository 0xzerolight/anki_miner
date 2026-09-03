"""One spelling for "which binary did this process actually pick, and why not".

Every external tool the app runs — yt-dlp, ffmpeg, ffprobe, alass, libmpv —
comes from a first-hit-wins tier list (config override, app-managed download,
PATH, the frozen bundle, the interpreter sibling, a bare literal). Which tier
won is invisible from the outside: the two most expensive support threads this
project has had were "I updated yt-dlp but nothing changed" (a managed binary
silently rejected for a receipt mismatch, so the stale PATH copy kept running)
and "ffmpeg not found" from a user who had set ``ffmpeg_location`` to a file
without the executable bit. Both are one line of provenance away from trivial.

Two records, both grep anchors:

- ``<tool> resolved: tier= path= verified=`` — INFO, the winning tier.
- ``<tool> resolution refused: reason= ...`` — WARNING, a tier that could have
  won and did not, with the fields that name the rejected candidate.

**Deduplication.** Resolution runs on every spawn and every availability probe,
so an undeduped receipt would be the loudest line in the log while saying the
same thing each time. :func:`log_resolution` keeps the last
``(tier, path, verified)`` per tool and re-logs only when that tuple changes, so
a stable process emits exactly one line per tool and a *changed* resolution —
the interesting event — always emits a new one.

Refusals are NOT deduped: the level is fixed at WARNING and every call emits.
They stay bounded because callers only refuse from a cache-miss path, where a
rejected candidate is recomputed at most once per resolver-cache generation.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from anki_miner.utils.logging_ext import log_summary

__all__ = ["log_resolution", "log_resolution_refused"]

# Last logged (tier, path, verified) per tool. Process-global on purpose: the
# question it answers ("has this process already said where yt-dlp comes
# from?") is process-scoped, and the resolvers it serves cache globally too.
_LAST: dict[str, tuple[str, str, bool | None]] = {}
_LOCK = threading.Lock()


def _reset_for_tests() -> None:
    """Forget every remembered resolution (autouse fixture in tests/conftest.py).

    Without this a test that asserts "exactly one INFO" would pass or fail on
    whether an earlier test in the same xdist worker already logged the same
    tuple — the leaked-global class the resolver ``_CACHE`` fixture exists for.
    """
    with _LOCK:
        _LAST.clear()


def log_resolution(
    log: logging.Logger,
    tool: str,
    tier: str,
    path: str,
    *,
    verified: bool | None = None,
    level: int = logging.INFO,
) -> None:
    """Record which tier resolved *tool*, once per distinct outcome.

    Args:
        log: Caller's module logger, so the record keeps the resolver's identity.
        tool: Tool name as the user knows it (``yt-dlp``, ``ffmpeg``, ``alass``).
        tier: Which tier won — ``override``, ``managed``, ``path``, ``bundled``,
            ``sibling``, or ``literal``.
        path: The resolved path or bare literal.
        verified: Tri-state receipt status: ``True``/``False`` where a tier
            carries a verification receipt, ``None`` where verification does not
            apply (rendered ``-``).
        level: Normally INFO; a caller reporting a degraded pick may raise it.
    """
    outcome = (tier, path, verified)
    with _LOCK:
        if _LAST.get(tool) == outcome:
            return
        _LAST[tool] = outcome
    log_summary(
        log,
        f"{tool} resolved",
        level=level,
        tier=tier,
        path=Path(path),
        verified=verified,
    )


def log_resolution_refused(log: logging.Logger, tool: str, reason: str, **fields: object) -> None:
    """Record a candidate that a resolver rejected, always at WARNING.

    A refusal changes what the user gets — a stale binary, a missing tool, a
    fail-closed error — so it is user-visible by the level rule and never
    DEBUG. ``reason`` is a stable machine token (``receipt_mismatch``,
    ``receipt_unreadable``, ``receipt_malformed``, ``override_not_executable``,
    …); the remaining fields name the rejected candidate.

    Args:
        log: Caller's module logger.
        tool: Tool name as the user knows it.
        reason: Stable snake_case token for the rejection.
        **fields: Ordered detail fields, rendered after ``reason``.
    """
    log_summary(log, f"{tool} resolution refused", level=logging.WARNING, reason=reason, **fields)

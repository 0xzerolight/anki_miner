"""One spelling for subprocess argv, spawn-failure and failure-tail records.

A failed external tool (yt-dlp, ffmpeg, alass, ffsubsync) is only diagnosable
from a user's log when the record carries the argv that produced it. Callers
used to format their own, so most logged the tool name and nothing else, and
the spawn ``OSError`` inside :func:`~anki_miner.utils.process_supervisor.run_supervised`
was returned without a single line -- the shape of the Windows yt-dlp report
where the app reported a generic failure for a binary that was never executed.

Masking is deliberately narrow (locked decision, 2026-09-03): diagnosis comes
first, so paths, URLs, cookie *file paths*, format selectors and every other
token are logged verbatim. Only credentials are erased -- the values of the
``--username``/``--password`` family and URL userinfo -- because those are
secrets and nothing about them helps a diagnosis.
"""

from __future__ import annotations

import logging
import os
import shlex
from collections.abc import Callable, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

# Failure tails are diagnostic buffers, not transcripts: the last 20 lines
# carry the tool's error, and everything above it is the run that led there.
STDERR_TAIL_LINES = 20

_MASK = "<masked>"

# yt-dlp's credential flags. Every one takes a value, so the token AFTER the
# flag is the secret; the ``--flag=VALUE`` spelling carries it inline instead.
_MASKED_VALUE_FLAGS = frozenset(
    {
        "--username",
        "--password",
        "--ap-username",
        "--ap-password",
        "--video-password",
        "--client-certificate-password",
    }
)


def _strip_userinfo(token: str) -> str:
    """Drop ``user:password@`` from a token that parses as a URL with userinfo."""
    try:
        parts = urlsplit(token)
    except ValueError:
        # Malformed IPv6 brackets and friends: not a URL we can rewrite, and a
        # token we cannot parse is a token we log verbatim.
        return token
    if not parts.scheme or "@" not in parts.netloc:
        return token
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def mask_argv(argv: Iterable[str | os.PathLike[str]]) -> list[str]:
    """Render *argv* as strings with credential values replaced by ``<masked>``.

    Everything else survives verbatim, including paths (a ``--cookies`` path is
    a path, not a cookie), format selectors and URLs without userinfo.
    """
    masked: list[str] = []
    mask_next = False
    for raw in argv:
        token = os.fspath(raw) if isinstance(raw, os.PathLike) else str(raw)
        if mask_next:
            masked.append(_MASK)
            mask_next = False
            continue
        if token in _MASKED_VALUE_FLAGS:
            mask_next = True
            masked.append(token)
            continue
        flag, separator, _value = token.partition("=")
        if separator and flag in _MASKED_VALUE_FLAGS:
            masked.append(f"{flag}={_MASK}")
            continue
        masked.append(_strip_userinfo(token))
    return masked


def log_command(
    log: logging.Logger,
    op: str,
    argv: Iterable[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout_s: float | None = None,
    level: int = logging.DEBUG,
) -> None:
    """Record the argv of one external command before it runs.

    DEBUG by default because a healthy run spawns many processes; the spawn and
    failure paths re-log at WARNING so a broken run carries the argv even when
    the user never enabled debug logging.

    Args:
        log: Caller's module logger, keeping the record attributed to the caller.
        op: Short operation label -- normally the tool name, the grep anchor.
        argv: Command as passed to ``Popen``; masked by :func:`mask_argv`.
        cwd: Working directory, or ``None`` when the process inherits it.
        timeout_s: Supervision budget in seconds, or ``None`` when unbounded.
        level: Logging level for this record.
    """
    # stacklevel=2 so %(lineno)d resolves to the caller, not to this helper.
    log.log(
        level,
        "%s: argv=%s cwd=%s timeout=%ss",
        op,
        shlex.join(mask_argv(argv)),
        cwd if cwd is not None else "-",
        timeout_s if timeout_s is not None else "-",
        stacklevel=2,
    )


def log_command_result(
    log: logging.Logger,
    op: str,
    argv: Iterable[str | os.PathLike[str]],
    *,
    returncode: int | None,
    state: str = "completed",
    stderr_tail: Sequence[str] = (),
    elapsed_s: float,
    level: int = logging.DEBUG,
) -> None:
    """Record how one external command ended, with its output tail on failure.

    A success is one short DEBUG line: the argv was already recorded by
    :func:`log_command`, and repeating it per successful process is noise. A
    non-success repeats the argv, because the failure record is the one a user
    pastes into a report and it has to stand alone.

    Args:
        log: Caller's module logger.
        op: Same operation label passed to :func:`log_command`.
        argv: Command that ran; masked by :func:`mask_argv`.
        returncode: Process exit code, or ``None`` when it never reported one.
        state: Terminal state name (``SupervisedState.value`` at the caller).
        stderr_tail: Bounded output tail from :func:`tail_for_log`.
        elapsed_s: Wall time from spawn to terminal state.
        level: Logging level; WARNING for user-visible failures, else DEBUG.
    """
    if state == "completed" and returncode == 0:
        log.log(level, "%s ok: rc=0 elapsed=%.2fs", op, elapsed_s, stacklevel=2)
        return
    # The tail goes in as an argument, never concatenated into the format
    # string: a stderr line containing '%' would otherwise break %-formatting.
    tail_block = "".join(f"\n  {line}" for line in stderr_tail)
    log.log(
        level,
        "%s failed: state=%s rc=%s elapsed=%.2fs argv=%s%s",
        op,
        state,
        returncode if returncode is not None else "-",
        elapsed_s,
        shlex.join(mask_argv(argv)),
        tail_block,
        stacklevel=2,
    )


def tail_for_log(
    text: str,
    *,
    noise_filter: Callable[[str], bool] | None = None,
    limit: int = STDERR_TAIL_LINES,
) -> list[str]:
    """Return the last *limit* meaningful lines of *text*.

    Blank lines are dropped, and *noise_filter* drops whatever the caller knows
    is noise -- progress ticks evict the actual error otherwise, the failure
    documented in ``ytdlp_invocation.is_progress_only``.

    Args:
        text: Captured stderr (or combined output) of the failed process.
        noise_filter: Predicate returning ``True`` for a line to drop.
        limit: Maximum number of lines to keep.
    """
    if limit <= 0:
        return []
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if noise_filter is not None and noise_filter(line):
            continue
        kept.append(line)
    return kept[-limit:]

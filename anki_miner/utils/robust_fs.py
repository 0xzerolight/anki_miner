"""Bounded, Windows-aware recursive deletion."""

from __future__ import annotations

import errno
import logging
import os
import random
import shutil
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeAlias, overload

logger = logging.getLogger(__name__)

RmtreeOutcome: TypeAlias = tuple[bool, OSError | None]
RmtreeMode: TypeAlias = Literal["raise", "outcome"]

_LOCK_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY})
_WINDOWS_LOCK_WINERRORS = frozenset({32, 33})


def _default_jitter(delay: float) -> float:
    return delay * random.uniform(0.8, 1.2)


def _is_retryable(error: OSError) -> bool:
    return (
        isinstance(error, PermissionError)
        or error.errno in _LOCK_ERRNOS
        or getattr(error, "winerror", None) in _WINDOWS_LOCK_WINERRORS
    )


def _chmod_and_retry(func: Callable[[str], Any], path: str, error: BaseException) -> None:
    if not isinstance(error, OSError) or not (
        isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.EPERM}
    ):
        raise error
    mode = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, mode | stat.S_IWUSR)
    func(path)


def _on_rmtree_error(
    func: Callable[[str], Any],
    path: str,
    exc_info: tuple[type[BaseException], BaseException, object],
) -> None:
    _chmod_and_retry(func, path, exc_info[1])


def _on_rmtree_exc(
    func: Callable[[str], Any],
    path: str,
    error: BaseException,
) -> None:
    _chmod_and_retry(func, path, error)


def _rmtree_once(target: Path) -> None:
    rmtree = shutil.rmtree
    if sys.version_info >= (3, 12):
        rmtree(target, onexc=_on_rmtree_exc)  # type: ignore[call-arg]
    else:
        rmtree(target, onerror=_on_rmtree_error)


@overload
def robust_rmtree(
    target: Path,
    *,
    mode: Literal["raise"] = "raise",
    deadline_s: float = 3.0,
    initial_backoff_s: float = 0.05,
    max_backoff_s: float = 0.5,
    jitter: Callable[[float], float] = _default_jitter,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None: ...


@overload
def robust_rmtree(
    target: Path,
    *,
    mode: Literal["outcome"],
    deadline_s: float = 3.0,
    initial_backoff_s: float = 0.05,
    max_backoff_s: float = 0.5,
    jitter: Callable[[float], float] = _default_jitter,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RmtreeOutcome: ...


def robust_rmtree(
    target: Path,
    *,
    mode: RmtreeMode = "raise",
    deadline_s: float = 3.0,
    initial_backoff_s: float = 0.05,
    max_backoff_s: float = 0.5,
    jitter: Callable[[float], float] = _default_jitter,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None | RmtreeOutcome:
    """Delete *target* with bounded retries.

    ``mode="raise"`` is for deletion that must succeed before a commit.
    ``mode="outcome"`` is for post-commit and ``finally`` cleanup: deletion
    errors are logged and returned as ``(False, error)``.
    """
    if mode not in ("raise", "outcome"):
        raise ValueError(f"Unknown robust_rmtree mode: {mode}")

    stop_at = clock() + max(0.0, deadline_s)
    backoff = max(0.0, initial_backoff_s)
    max_backoff = max(backoff, max_backoff_s)

    while True:
        try:
            _rmtree_once(target)
        except FileNotFoundError as error:
            if not os.path.lexists(target):
                return (True, None) if mode == "outcome" else None
            last_error: OSError = error
        except OSError as error:
            last_error = error
        else:
            return (True, None) if mode == "outcome" else None

        now = clock()
        if not _is_retryable(last_error) or now >= stop_at:
            if mode == "raise":
                raise last_error
            logger.warning("robust delete left residue at %s: %s", target, last_error)
            return False, last_error

        remaining = stop_at - now
        delay = min(max(0.0, jitter(backoff)), remaining)
        if delay <= 0.0:
            if mode == "raise":
                raise last_error
            logger.warning("robust delete left residue at %s: %s", target, last_error)
            return False, last_error
        sleep(delay)
        backoff = min(max_backoff, backoff * 2.0)

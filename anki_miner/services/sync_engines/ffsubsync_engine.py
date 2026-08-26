"""ffsubsync as a sync engine (supervised child process, primary).

ffsubsync ships as a pure-Python library with a documented API
(``ffsubsync.run``), which sidesteps every problem the alass binary has: it
needs no per-platform binary (macOS gets retiming for the first time), reports
a machine-readable verdict (``sync_was_successful``, ``offset_seconds``,
``framerate_scale_factor``), and carries its own low-quality gate
(``--skip-sync-on-low-quality``).

Invocation notes:

* Args are built as an ffsubsync CLI argv and parsed in the child through
  ``make_parser().parse_args([...])``, so this module tracks the CLI contract
  exactly.
* ``--ffmpeg-path`` accepts a full ffmpeg binary path; ffprobe is resolved as
  its sibling.
* ``--split-penalty`` (0.5.x) enables alass-style piecewise sync; without it
  ffsubsync applies one offset + optional framerate scale.
* The library call happens **out of process**, under
  :func:`~anki_miner.utils.process_supervisor.run_supervised`. In-process, the
  API has no timeout and no cancellation, and it spawns its own untracked
  ffmpeg — a pathological audio reference pinned the retime worker with a
  grandchild nothing could kill. Supervision bounds the run and terminates the
  whole process tree on cancel or timeout.
* ffsubsync 0.5.1 has no ``__main__``, and a frozen bundle carries no
  interpreter, so the child is *this application* re-entered through
  ``gui/launch.py``'s ``--ffsubsync-child`` dispatch — see
  :mod:`~anki_miner.services.sync_engines._ffsubsync_child`, which prints the
  verdict as one JSON line because an exit code alone cannot carry it (a
  low-quality reject writes the original subtitles and exits 0).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from anki_miner.services.sync_engines import SyncResult
from anki_miner.services.sync_engines._ffsubsync_child import CHILD_FLAG
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised

logger = logging.getLogger(__name__)

__all__ = ["sync_with_ffsubsync"]

#: ffsubsync's own quality gate: reject syncs whose best offset exceeds this.
#: Deliberately tighter than the validator's five-minute bound — ffsubsync
#: scores against the whole reference, so a huge winning offset means the
#: score landscape is flat and untrustworthy.
_QUALITY_MAX_OFFSET_S = 120.0

#: Matches alass's bound: a retime that has run for an hour is stuck, not slow.
_FFSUBSYNC_TIMEOUT_S = 60 * 60


def _child_command(argv: list[str]) -> list[str]:
    """The argv that re-enters this application as the ffsubsync child."""
    if getattr(sys, "frozen", False):
        return [sys.executable, CHILD_FLAG, *argv]
    return [sys.executable, "-m", "anki_miner", CHILD_FLAG, *argv]


def _parse_verdict(stdout: str) -> dict[str, Any] | None:
    """Return the child's verdict line, or ``None`` when it never printed one."""
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _number_or_none(value: object) -> float | None:
    """Keep :class:`SyncResult`'s float fields floats: the verdict crosses JSON."""
    return float(value) if isinstance(value, (int, float)) else None


def sync_with_ffsubsync(
    config,
    reference: Path,
    in_sub: Path,
    out: Path,
    *,
    split_mode: bool = True,
    split_penalty: float = 8.0,
    reference_stream: str | None = None,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> SyncResult:
    """Run ffsubsync in a supervised child and write its output to *out*.

    ``ok`` requires both a clean return and ffsubsync's own
    ``sync_was_successful`` verdict (its internal quality gate is enabled), so
    a low-confidence sync comes back rejected rather than silently written.
    Never raises: a crashed, cancelled or timed-out child is a failed
    candidate, and the caller falls through to the next engine.
    """
    engine = "ffsubsync (single offset)" if not split_mode else "ffsubsync"

    if cancel_event is not None and cancel_event.is_set():
        return SyncResult(ok=False, engine=engine, detail="cancelled")

    argv = [
        str(reference),
        "-i",
        str(in_sub),
        "-o",
        str(out),
        "--ffmpeg-path",
        resolve_ffmpeg(config),
        "--skip-sync-on-low-quality",
        "--quality-max-offset-seconds",
        str(_QUALITY_MAX_OFFSET_S),
    ]
    if split_mode:
        argv += ["--split-penalty", str(split_penalty)]
    if reference_stream is not None:
        argv += ["--reference-stream", reference_stream]

    supervised = run_supervised(
        _child_command(argv),
        timeout_s=_FFSUBSYNC_TIMEOUT_S,
        cancel=cancel_event,
        retain_output=False,
    )

    if supervised.state in {SupervisedState.CANCELLED, SupervisedState.TIMED_OUT}:
        _unlink_quiet(out)
        return SyncResult(ok=False, engine=engine, detail=supervised.state.value)

    result = _parse_verdict(supervised.stdout) if supervised.state is SupervisedState.COMPLETED else None
    if result is None:
        _unlink_quiet(out)
        tail = "\n".join(supervised.stderr.splitlines()[-50:])
        logger.warning(
            "ffsubsync failed on %s (%s, exit %s). Last output:\n%s",
            in_sub.name,
            supervised.state.value,
            supervised.returncode,
            tail,
        )
        return SyncResult(ok=False, engine=engine, detail=f"{supervised.state.value}, exit {supervised.returncode}")

    successful = bool(result.get("sync_was_successful")) and result.get("retval", 1) == 0
    offset = _number_or_none(result.get("offset_seconds"))
    scale = _number_or_none(result.get("framerate_scale_factor"))
    if log_cb is not None and offset is not None:
        log_cb(f"ffsubsync offset {offset:+.3f}s, framerate scale {scale or 1.0:.4f}")

    if not successful or not out.exists():
        _unlink_quiet(out)
        return SyncResult(
            ok=False,
            engine=engine,
            offset_seconds=offset,
            framerate_scale=scale,
            detail="low-quality sync rejected" if result.get("retval", 1) == 0 else "ffsubsync error",
        )

    return SyncResult(
        ok=True,
        engine=engine,
        offset_seconds=offset,
        framerate_scale=scale,
    )


def _unlink_quiet(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass

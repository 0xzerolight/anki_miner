"""ffsubsync as a sync engine (in-process, primary).

ffsubsync ships as a pure-Python library with a documented API
(``ffsubsync.run``), which sidesteps every problem the alass binary has: it
needs no per-platform binary (macOS gets retiming for the first time), reports
a machine-readable verdict (``sync_was_successful``, ``offset_seconds``,
``framerate_scale_factor``), and carries its own low-quality gate
(``--skip-sync-on-low-quality``).

Invocation notes:

* Args are built through ``make_parser().parse_args([...])`` so this module
  tracks the CLI contract exactly.
* ``--ffmpeg-path`` accepts a full ffmpeg binary path; ffprobe is resolved as
  its sibling.
* ``--split-penalty`` (0.5.x) enables alass-style piecewise sync; without it
  ffsubsync applies one offset + optional framerate scale.
* In-process means cancellation cannot interrupt a run mid-flight; the
  orchestrator checks its cancel event between pipeline steps instead.
  Sub-to-sub references finish in well under a second, so the window where
  this matters is the audio-reference path only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from anki_miner.services.sync_engines import SyncResult
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg

logger = logging.getLogger(__name__)

__all__ = ["sync_with_ffsubsync"]

#: ffsubsync's own quality gate: reject syncs whose best offset exceeds this.
#: Deliberately tighter than the validator's five-minute bound — ffsubsync
#: scores against the whole reference, so a huge winning offset means the
#: score landscape is flat and untrustworthy.
_QUALITY_MAX_OFFSET_S = 120.0


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
    """Run ffsubsync in-process and write its output to *out*.

    ``ok`` requires both a clean return and ffsubsync's own
    ``sync_was_successful`` verdict (its internal quality gate is enabled), so
    a low-confidence sync comes back rejected rather than silently written.
    Never raises: any exception out of ffsubsync is a failed candidate.
    """
    if cancel_event is not None and cancel_event.is_set():
        return SyncResult(ok=False, engine="ffsubsync", detail="cancelled")

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

    try:
        # Deferred import: ffsubsync pulls numpy and its VAD stack; the GUI
        # imports this module's callers at startup.
        from ffsubsync.ffsubsync import make_parser
        from ffsubsync.ffsubsync import run as ffsubsync_run

        args = make_parser().parse_args(argv)
        result = ffsubsync_run(args)
    except Exception as exc:  # noqa: BLE001 — an engine crash is a failed candidate, never fatal
        logger.warning("ffsubsync failed on %s", in_sub.name, exc_info=True)
        _unlink_quiet(out)
        return SyncResult(ok=False, engine="ffsubsync", detail=f"raised {type(exc).__name__}: {exc}")

    successful = bool(result.get("sync_was_successful")) and result.get("retval", 1) == 0
    offset = result.get("offset_seconds")
    scale = result.get("framerate_scale_factor")
    if log_cb is not None and offset is not None:
        log_cb(f"ffsubsync offset {offset:+.3f}s, framerate scale {scale or 1.0:.4f}")

    if not successful or not out.exists():
        _unlink_quiet(out)
        return SyncResult(
            ok=False,
            engine="ffsubsync",
            offset_seconds=offset,
            framerate_scale=scale,
            detail="low-quality sync rejected" if result.get("retval", 1) == 0 else "ffsubsync error",
        )

    return SyncResult(
        ok=True,
        engine="ffsubsync",
        offset_seconds=offset,
        framerate_scale=scale,
    )


def _unlink_quiet(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass

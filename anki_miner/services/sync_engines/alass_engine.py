"""alass as a sync engine.

alass CLI (v2.0.0) notes
------------------------
* Usage: ``alass [OPTIONS] <reference> <incorrect-sub> <output>``.
  Options come **before** the three positional paths.
* The reference may be a **video or a subtitle file**; sub-to-sub alignment is
  both more accurate and far faster than aligning against audio.
* ``--split-penalty <float>``  (0–1000, default 7) goes before the positionals.
* ``--speed-optimization`` defaults to 1 and, per ``--help``, "(greatly) speeds
  up synchronization by sacrificing some accuracy".  0 disables it.
* ``--encoding-inc`` / ``--encoding-ref`` take **WHATWG labels** and alass
  *panics* on any label it does not know (``cp932`` panics, ``shift_jis``
  works), so only vetted labels may be passed — see
  :func:`~anki_miner.utils.subtitle_encoding.detect_subtitle_encoding`.
* The v2 flag is ``--no-split``, singular; the README's ``--no-splits`` is stale.
* ``--disable-fps-guessing`` matters: alass "guesses" framerate ratios from six
  fixed candidates and misfires even on same-framerate pairs (observed: a clean
  two-block fixture came back as a twelve-block staircase under a wrongly
  guessed 25/24). Guessing stays off in this pipeline.
* Output format is inferred from the output file's extension.
* All output (progress, errors) goes to **stdout**; stderr is empty.
  Merge stderr → stdout via ``stderr=subprocess.STDOUT``.
* Exit 0 = success; nonzero = failure.
* Per-block results are printed as
  ``shifted block of N subtitles with length H:MM:SS.mmm by -H:MM:SS.mmm`` —
  parsed here into :class:`SyncResult.block_shifts_seconds` so the validator
  can judge dispersion and magnitude.
* alass shells out to ffmpeg/ffprobe internally; point it at our resolved
  binaries via ``ALASS_FFMPEG_PATH`` / ``ALASS_FFPROBE_PATH`` env vars.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.services.sync_engines import SyncResult
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised
from anki_miner.utils.subtitle_encoding import detect_subtitle_encoding

logger = logging.getLogger(__name__)

__all__ = ["sync_with_alass"]

_ALASS_TIMEOUT_S = 60 * 60

_BLOCK_SHIFT_RE = re.compile(r"shifted block of \d+ subtitles with length \S+ by\s+(-?)(\d+):(\d{2}):(\d{2})\.(\d{3})")
_FPS_RATIO_RE = re.compile(r"ratio is (\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)")


def _parse_block_shifts(stdout: str) -> tuple[float, ...]:
    shifts = []
    for match in _BLOCK_SHIFT_RE.finditer(stdout):
        sign, hours, minutes, seconds, millis = match.groups()
        value = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
        shifts.append(-value if sign else value)
    return tuple(shifts)


def _parse_warnings(stdout: str) -> tuple[str, ...]:
    warnings = []
    for line in stdout.splitlines():
        if line.startswith("warn:") and "negative" in line:
            warnings.append(line.strip())
    match = _FPS_RATIO_RE.search(stdout)
    if match and match.group(1) != match.group(2):
        warnings.append(f"framerate ratio guessed as {match.group(1)}/{match.group(2)}")
    return tuple(warnings)


def sync_with_alass(
    config,
    reference: Path,
    in_sub: Path,
    out: Path,
    *,
    sub_reference: bool,
    no_split: bool = False,
    split_penalty: float = 7.0,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> SyncResult:
    """Run alass and write its output to *out*.

    ``ok`` means alass exited 0 and *out* exists — nothing more; quality is the
    validator's call. Cancellation/timeout also come back as ``ok=False`` with
    the state in ``detail``.

    Raises:
        AlassNotFoundError: When the alass binary cannot be found (macOS has no
            bundled binary). The orchestrator treats this as engine-unavailable.
    """
    alass_bin = resolve_alass(config)

    flags: list[str] = ["--disable-fps-guessing"]
    if no_split:
        flags.append("--no-split")

    # The input subtitle's encoding is independent of what it is aligned
    # against, so this is declared on both paths. alass's own detection fails
    # outright on cp932 ("error while decoding subtitle from bytes to string"),
    # which is the routine encoding for Japanese subtitle downloads.
    incoming = detect_subtitle_encoding(in_sub)
    if incoming is not None:
        flags += ["--encoding-inc", incoming]

    if sub_reference:
        # Sub-to-sub alignment finishes in well under a second, so alass's
        # accuracy-for-speed tradeoff buys nothing and is turned off. The audio
        # path keeps the default: there it can cost minutes on a full episode.
        flags += ["--speed-optimization", "0"]
        # clean_reference always writes UTF-8, so the reference encoding is
        # known exactly rather than guessed.
        flags += ["--encoding-ref", "utf-8"]

    cmd = [
        alass_bin,
        *flags,
        "--split-penalty",
        str(split_penalty),
        str(reference),
        str(in_sub),
        str(out),
    ]

    env = os.environ.copy()
    env["ALASS_FFMPEG_PATH"] = resolve_ffmpeg(config)
    env["ALASS_FFPROBE_PATH"] = resolve_ffprobe(config)

    result = run_supervised(
        cmd,
        timeout_s=_ALASS_TIMEOUT_S,
        cancel=cancel_event,
        env=env,
        line_callback=log_cb,
        combine_stderr=True,
    )
    if isinstance(result.error, FileNotFoundError):
        raise AlassNotFoundError(
            f"alass binary not found: {alass_bin!r}.  Install alass or set its path in Settings → Transcription & Alignment."
        ) from result.error

    engine = "alass (single offset)" if no_split else "alass"

    if result.state in {SupervisedState.CANCELLED, SupervisedState.TIMED_OUT}:
        _unlink_quiet(out)
        return SyncResult(ok=False, engine=engine, detail=result.state.value)

    if result.state is SupervisedState.COMPLETED and out.exists():
        return SyncResult(
            ok=True,
            engine=engine,
            block_shifts_seconds=_parse_block_shifts(result.stdout),
            warnings=_parse_warnings(result.stdout),
        )

    _unlink_quiet(out)
    tail = "\n".join(result.stdout.splitlines()[-50:])
    logger.warning(
        "alass retiming failed (%s, exit %s). Last output:\n%s",
        result.state.value,
        result.returncode,
        tail,
    )
    return SyncResult(
        ok=False,
        engine=engine,
        detail=f"{result.state.value}, exit {result.returncode}",
    )


def _unlink_quiet(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass

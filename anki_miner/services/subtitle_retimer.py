"""Subtitle retiming via the external ``alass`` binary.

alass aligns an off-timed subtitle file to a reference.  This module wraps the
subprocess interaction, wires up cancellation, and streams progress lines to an
optional callback.  Choosing and preparing the reference — an embedded subtitle
track where one exists, extracted audio otherwise — lives in
:mod:`anki_miner.services.retime_reference`.

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
* Output format is inferred from the output file's extension.
* All output (progress, errors) goes to **stdout**; stderr is empty.
  Merge stderr → stdout via ``stderr=subprocess.STDOUT``.
* Exit 0 = success; nonzero = failure.
* alass shells out to ffmpeg/ffprobe internally; point it at our resolved
  binaries via ``ALASS_FFMPEG_PATH`` / ``ALASS_FFPROBE_PATH`` env vars.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.services.retime_reference import ReferenceOverride, resolve_reference
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised
from anki_miner.utils.subtitle_encoding import detect_subtitle_encoding

logger = logging.getLogger(__name__)

__all__ = ["retime_subtitle"]

_ALASS_TIMEOUT_S = 60 * 60


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def retime_subtitle(
    config,
    video: Path,
    in_sub: Path,
    out_sub: Path,
    *,
    split_penalty: float = 7,
    disable_fps_guessing: bool = True,
    no_split: bool = False,
    reference_override: ReferenceOverride | None = None,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> bool:
    """Retime *in_sub* to *video* using alass, writing the result to *out_sub*.

    Uses a temp file in ``out_sub.parent`` with the same extension so that the
    final atomic ``os.replace(tmp, out_sub)`` stays on the same filesystem, and
    so the ``in_sub == out_sub`` aliasing case is handled safely (alass reads
    *in_sub*, writes the distinct temp path, then we replace).

    The reference comes from
    :func:`~anki_miner.services.retime_reference.resolve_reference`: a cleaned
    copy of an embedded subtitle track when *video* has a usable one, otherwise
    a pre-extracted audio WAV. If both fail, *video* itself is passed and alass
    does its own audio extraction, so nothing ever blocks a run.

    Args:
        config: Application config (used to resolve alass/ffmpeg/ffprobe paths).
        video:  The video the subtitle should be matched against.
        in_sub: The off-timed subtitle file to correct.
        out_sub: Destination path for the corrected subtitle.
        split_penalty: alass ``--split-penalty`` value (0–1000, default 7).
            Lower values allow more split-points; higher keeps the sub as one
            contiguous block.
        disable_fps_guessing: When True (default), pass ``--disable-fps-guessing``
            so alass never multiplicatively stretches the sub to a guessed
            framerate ratio. Correct for resyncing a sub to *its own* video; set
            False only for subs from a different-framerate release.
        no_split: When True, pass ``--no-split`` so alass applies a single global
            offset instead of cutting the sub into independently-shifted segments.
        reference_override: Explicit user pick of the reference track; None
            auto-selects (embedded subtitle preferred, audio fallback).
        cancel_event: When set, kills the alass process group and returns False.
        log_cb: Called with each stripped stdout line from alass as it arrives,
            and with the reference-selection decisions made before it starts.

    Returns:
        True on success (alass exited 0 and output was written); False on any
        failure or cancellation.

    Raises:
        AlassNotFoundError: When the alass binary cannot be found.  This is the
            **only** exception this function may raise.
    """
    alass_bin = resolve_alass(config)

    # Build the temp output path: same directory and extension as out_sub so
    # os.replace is atomic (same filesystem) and alass infers the right format.
    tmp_out = out_sub.parent / (out_sub.stem + ".retime-tmp" + out_sub.suffix)

    reference = resolve_reference(
        config,
        video,
        override=reference_override,
        cancel_event=cancel_event,
        log_cb=log_cb,
    )

    try:
        return _run_alass(
            alass_bin,
            config,
            reference.path if reference is not None else video,
            in_sub,
            tmp_out,
            out_sub,
            split_penalty=split_penalty,
            disable_fps_guessing=disable_fps_guessing,
            no_split=no_split,
            sub_reference=reference is not None and reference.kind == "subtitle",
            cancel_event=cancel_event,
            log_cb=log_cb,
        )
    finally:
        if reference is not None and reference.temp is not None:
            with contextlib.suppress(OSError):
                reference.temp.unlink()


def _run_alass(
    alass_bin: str,
    config,
    reference: Path,
    in_sub: Path,
    tmp_out: Path,
    out_sub: Path,
    *,
    split_penalty: float,
    disable_fps_guessing: bool,
    no_split: bool,
    sub_reference: bool,
    cancel_event: threading.Event | None,
    log_cb: Callable[[str], None] | None,
) -> bool:
    """Run alass against *reference* and place the corrected sub at *out_sub*."""
    flags: list[str] = []
    if disable_fps_guessing:
        flags.append("--disable-fps-guessing")
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
        # _clean_reference always writes UTF-8, so the reference encoding is
        # known exactly rather than guessed.
        flags += ["--encoding-ref", "utf-8"]

    cmd = [
        alass_bin,
        *flags,
        "--split-penalty",
        str(split_penalty),
        str(reference),
        str(in_sub),
        str(tmp_out),
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

    # --- Evaluate result -------------------------------------------------
    if result.state in {SupervisedState.CANCELLED, SupervisedState.TIMED_OUT}:
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except OSError:
            pass
        return False

    if result.state is SupervisedState.COMPLETED and tmp_out.exists():
        os.replace(tmp_out, out_sub)
        return True

    # Failure — clean up the partial temp file and log the tail.
    try:
        if tmp_out.exists():
            tmp_out.unlink()
    except OSError:
        pass

    logger.warning(
        "alass retiming failed (%s, exit %s). Last output:\n%s",
        result.state.value,
        result.returncode,
        "\n".join(result.stdout.splitlines()[-50:]),
    )
    return False

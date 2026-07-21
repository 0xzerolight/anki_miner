"""Subtitle retiming via the external ``alass`` binary.

alass aligns an off-timed subtitle file to a reference video by analysing the
audio track.  This module wraps the subprocess interaction, wires up
cancellation, and streams progress lines to an optional callback.

alass CLI (v2.0.0) notes
------------------------
* Usage: ``alass [OPTIONS] <reference> <incorrect-sub> <output>``.
  Options come **before** the three positional paths.
* ``--split-penalty <float>``  (0–1000, default 7) goes before the positionals.
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
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised

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
    audio_track_override: int | None = None,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> bool:
    """Retime *in_sub* to *video* using alass, writing the result to *out_sub*.

    Uses a temp file in ``out_sub.parent`` with the same extension so that the
    final atomic ``os.replace(tmp, out_sub)`` stays on the same filesystem, and
    so the ``in_sub == out_sub`` aliasing case is handled safely (alass reads
    *in_sub*, writes the distinct temp path, then we replace).

    Audio reference: rather than letting alass pick an audio stream internally
    (it has no CLI flag for this, so on dual-audio anime it may align against the
    English dub and mangle a Japanese sub), this pre-extracts the chosen audio
    track to a temp 16 kHz mono WAV via
    :meth:`MediaExtractorService.extract_full_audio` (which auto-detects the
    Japanese track when *audio_track_override* is None, falling back to the first
    track) and hands alass that WAV as the reference. If extraction fails for any
    reason it falls back to passing *video* directly, so a probe/ffmpeg hiccup
    never blocks retiming.

    Args:
        config: Application config (used to resolve alass/ffmpeg/ffprobe paths).
        video:  Reference video that alass analyses for audio timing.
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
        audio_track_override: Audio-stream index (0-indexed among audio streams)
            to align against; None auto-detects the Japanese track.
        cancel_event: When set, kills the alass process group and returns False.
        log_cb: Called with each stripped stdout line from alass as it arrives.

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

    # Pre-extract the chosen audio track to a temp WAV and feed alass that file
    # as the reference (see docstring). On extraction failure / cancel, fall back
    # to the raw video so retiming still proceeds.
    reference: Path = video
    ref_wav = _extract_reference_audio(config, video, audio_track_override, cancel_event)
    if ref_wav is not None:
        reference = ref_wav

    try:
        return _run_alass(
            alass_bin,
            config,
            reference,
            in_sub,
            tmp_out,
            out_sub,
            split_penalty=split_penalty,
            disable_fps_guessing=disable_fps_guessing,
            no_split=no_split,
            cancel_event=cancel_event,
            log_cb=log_cb,
        )
    finally:
        if ref_wav is not None:
            with contextlib.suppress(OSError):
                ref_wav.unlink()


def _extract_reference_audio(
    config,
    video: Path,
    audio_track_override: int | None,
    cancel_event: threading.Event | None,
) -> Path | None:
    """Extract the chosen audio track to a temp 16 kHz mono WAV for alass.

    Returns the WAV path on success, or None when extraction fails / is
    cancelled (callers then fall back to the raw video). Never raises.
    """
    if cancel_event is not None and cancel_event.is_set():
        return None

    # Lazy import keeps the heavy media-extractor module off this module's import
    # path and avoids any import cycle.
    from anki_miner.services.media_extractor import MediaExtractorService

    fd, tmp_name = tempfile.mkstemp(suffix=".retime-ref.wav")
    os.close(fd)
    tmp_wav = Path(tmp_name)
    try:
        ok = MediaExtractorService(config).extract_full_audio(
            video,
            tmp_wav,
            track_override=audio_track_override,
            cancel_event=cancel_event,
        )
    except Exception:  # noqa: BLE001 — extraction is best-effort; fall back to video
        logger.warning("retime: audio pre-extraction raised; using raw video", exc_info=True)
        ok = False

    if not ok:
        with contextlib.suppress(OSError):
            tmp_wav.unlink()
        return None
    return tmp_wav


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
    cancel_event: threading.Event | None,
    log_cb: Callable[[str], None] | None,
) -> bool:
    """Run alass against *reference* and place the corrected sub at *out_sub*."""
    flags: list[str] = []
    if disable_fps_guessing:
        flags.append("--disable-fps-guessing")
    if no_split:
        flags.append("--no-split")

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
            f"alass binary not found: {alass_bin!r}.  Install alass or set its path in Settings → Subtitles."
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

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

import collections
import contextlib
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.subprocess_utils import no_window_kwargs

logger = logging.getLogger(__name__)

__all__ = ["retime_subtitle"]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _proc_group_kwargs() -> dict:
    """Return subprocess kwargs that isolate the child into its own process group.

    On POSIX, ``start_new_session=True`` creates a new session/process-group so
    ``os.killpg`` can reach alass *and* its ffmpeg grandchild.  On Windows we
    use ``CREATE_NEW_PROCESS_GROUP`` for the same reason; ``CREATE_NO_WINDOW``
    suppresses the console flash (Issue #79).  Note: on Windows, grandchild
    reaping via the job-object is best-effort — ``proc.kill()`` sends
    ``TerminateProcess`` to alass only; the ffmpeg grandchild may linger until
    it detects the broken pipe, which is acceptable for this pass.
    """
    if sys.platform == "win32":
        # CREATE_NO_WINDOW already implies the no-window behaviour, so the
        # no_window_kwargs() spread would be redundant here — set creationflags
        # directly.
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        create_new_process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return {"creationflags": create_no_window | create_new_process_group}
    # POSIX: no_window_kwargs() returns {} but merge anyway for symmetry.
    return {**no_window_kwargs(), "start_new_session": True}


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

    # --- Launch -----------------------------------------------------------
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **_proc_group_kwargs(),
        )
    except FileNotFoundError as exc:
        raise AlassNotFoundError(
            f"alass binary not found: {alass_bin!r}.  " "Install alass or set its path in Settings → Subtitles."
        ) from exc

    # --- Cancellation watcher thread -------------------------------------
    # The watcher blocks until either cancel_event fires or the work is done.
    # A local done_event prevents it from holding the thread open after a
    # clean exit.
    done_event = threading.Event()
    cancel_thread: threading.Thread | None = None

    if cancel_event is not None:
        _ce: threading.Event = cancel_event
        _de: threading.Event = done_event
        _proc = proc

        def _watch() -> None:
            # Block until cancel fires OR work finishes (done_event set in finally).
            while not _ce.is_set() and not _de.is_set():
                _ce.wait(timeout=0.05)
            # Kill ONLY if cancel arrived while the process is still alive. Once
            # the main thread has reaped the process (done_event set after
            # proc.wait()), its PID may be reused — killing then could SIGKILL an
            # unrelated process group. The contextlib.suppress below still covers
            # the narrow race where the process exits between this check and the
            # kill syscall.
            if _ce.is_set() and not _de.is_set():
                # Kill the entire process group so alass's ffmpeg child dies too.
                if sys.platform != "win32":
                    with contextlib.suppress(ProcessLookupError, OSError):
                        os.killpg(os.getpgid(_proc.pid), signal.SIGKILL)
                else:
                    # Best-effort: TerminateProcess on alass; grandchild ffmpeg
                    # may not be reaped on Windows (acceptable for this pass).
                    with contextlib.suppress(OSError):
                        _proc.kill()

        cancel_thread = threading.Thread(target=_watch, daemon=True, name="alass-cancel-watcher")
        cancel_thread.start()

    # --- Stream stdout line by line --------------------------------------
    tail: collections.deque[str] = collections.deque(maxlen=50)

    if proc.stdout is None:
        # Should never happen with stdout=PIPE, but guard rather than assert
        # (asserts are stripped under python -O).
        done_event.set()
        if cancel_thread is not None:
            cancel_thread.join()
        return False

    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            tail.append(line)
            if log_cb is not None:
                log_cb(line)

        proc.wait()
    finally:
        # Signal the watcher that work is done before joining it.
        done_event.set()
        if cancel_thread is not None:
            cancel_thread.join()

    # --- Evaluate result -------------------------------------------------
    cancelled = cancel_event is not None and cancel_event.is_set()

    # Clean up and return False on cancel — even if alass happened to exit 0
    # before the kill reached it.
    if cancelled:
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except OSError:
            pass
        return False

    if proc.returncode == 0 and tmp_out.exists():
        os.replace(tmp_out, out_sub)
        return True

    # Failure — clean up the partial temp file and log the tail.
    try:
        if tmp_out.exists():
            tmp_out.unlink()
    except OSError:
        pass

    logger.warning(
        "alass retiming failed (exit %s). Last output:\n%s",
        proc.returncode,
        "\n".join(tail),
    )
    return False

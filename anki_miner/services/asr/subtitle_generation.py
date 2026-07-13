"""Per-file subtitle-generation pipeline (ARC-015).

Product policy that used to live on ``SubtitleGenWorker`` — the temp-WAV
lifecycle, the extract → load → transcribe → write orchestration, and the
"no recognised speech is a surfaced outcome, not a blank SRT" decision — lives
here as :func:`generate_subtitle_one`. It returns a STRUCTURED
:class:`SubtitleGenResult` (a status code plus the output path) rather than a
user-facing string; i18n stays in the GUI worker, which maps each
:class:`SubtitleGenStatus` back to a translated message. This keeps the pipeline
unit-testable without a QThread.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig

logger = logging.getLogger(__name__)


class SubtitleGenStatus(Enum):
    """Outcome of :func:`generate_subtitle_one` (mapped to a ``tr()`` message by the worker)."""

    SUCCESS = auto()
    #: ``extract_full_audio`` returned False for a non-cancel reason.
    EXTRACTION_FAILED = auto()
    #: Transcription returned no segments (silence / music-only track).
    NO_SPEECH = auto()
    #: A cancel landed during extraction, load, or transcription.
    CANCELLED = auto()


@dataclass(frozen=True)
class SubtitleGenResult:
    """Structured result of :func:`generate_subtitle_one`.

    ``out_srt`` is set only on :attr:`SubtitleGenStatus.SUCCESS`.
    """

    status: SubtitleGenStatus
    out_srt: Path | None = None


def generate_subtitle_one(
    config: AnkiMinerConfig,
    extractor,
    video_path: Path,
    out_srt: Path,
    *,
    on_extract_start: Callable[[], None] | None = None,
    transcribe_progress_cb: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> SubtitleGenResult:
    """Transcribe one video to an SRT at *out_srt*.

    Pipeline: extract full audio to a temp WAV → load to float32 → transcribe →
    write SRT. The temp WAV is always deleted before returning. Empty
    transcription yields :attr:`SubtitleGenStatus.NO_SPEECH` (a surfaced outcome,
    never a blank SRT). A cancel checked after each stage yields
    :attr:`SubtitleGenStatus.CANCELLED`.

    Args:
        config: Frozen application config (ASR model / device / roots).
        extractor: A ``MediaExtractorService`` (or stand-in) exposing
            ``extract_full_audio(video, out_wav, *, cancel_event=...)``.
        video_path: Source video to transcribe.
        out_srt: Destination SRT path (its parent is created if missing).
        on_extract_start: Called once right before extraction begins (the worker
            uses it to emit an "Extracting audio" progress line).
        transcribe_progress_cb: Forwarded to the transcriber as its
            ``progress_cb`` (called with a 0.0–1.0 fraction).
        cancel_event: Cooperative cancel, forwarded to extractor + transcriber.

    Unexpected exceptions propagate to the caller (the worker isolates them
    per-file); only the temp-WAV cleanup is guaranteed here.
    """
    # Lazy imports keep the heavy ASR / media-extractor modules off this module's
    # import path and let tests patch them at their canonical location.
    from anki_miner.services.asr import srt_writer, transcriber
    from anki_miner.services.media_extractor import wav_to_float32

    temp_dir = config.media_temp_folder
    temp_dir.mkdir(parents=True, exist_ok=True)

    # mkstemp gives a unique fd; close it and keep the path for ffmpeg to write.
    fd, tmp_wav_str = tempfile.mkstemp(prefix="asr_", suffix=".wav", dir=temp_dir)
    os.close(fd)
    tmp_wav = Path(tmp_wav_str)

    try:
        # --- Stage 1: extract audio ---
        if on_extract_start is not None:
            on_extract_start()

        ok = extractor.extract_full_audio(video_path, tmp_wav, cancel_event=cancel_event)
        if _is_cancelled(cancel_event):
            return SubtitleGenResult(SubtitleGenStatus.CANCELLED)
        if not ok:
            return SubtitleGenResult(SubtitleGenStatus.EXTRACTION_FAILED)

        # --- Stage 2: load audio ---
        audio, sample_rate, duration_s = wav_to_float32(tmp_wav)
        if _is_cancelled(cancel_event):
            return SubtitleGenResult(SubtitleGenStatus.CANCELLED)

        # --- Stage 3: transcribe ---
        segments = transcriber.transcribe(
            audio,
            model_name=config.asr_model,
            models_root=config.asr_models_root,
            sample_rate=sample_rate,
            duration_s=duration_s,
            cancel_event=cancel_event,
            progress_cb=transcribe_progress_cb,
            device=config.asr_device,
            cuda_libs_root=config.cuda_libs_root,
            onnx_pack_root=config.onnx_pack_root,
        )
        if _is_cancelled(cancel_event):
            return SubtitleGenResult(SubtitleGenStatus.CANCELLED)

        # No recognised speech: surface it rather than writing a blank SRT and
        # reporting a clean "Done". Empty audio is already rejected upstream by
        # extract_full_audio; this catches silence / music-only tracks.
        if not segments:
            logger.info("subtitle_generation: no speech detected in %s", video_path)
            return SubtitleGenResult(SubtitleGenStatus.NO_SPEECH)

        # --- Stage 4: write SRT ---
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        srt_writer.segments_to_srt(segments, out_srt)
        return SubtitleGenResult(SubtitleGenStatus.SUCCESS, out_srt=out_srt)
    finally:
        # Always clean up the temp WAV.
        try:
            if tmp_wav.exists():
                tmp_wav.unlink()
        except OSError:
            logger.warning("subtitle_generation: could not delete temp WAV %s", tmp_wav)


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    """True when *cancel_event* is present and set."""
    return cancel_event is not None and cancel_event.is_set()

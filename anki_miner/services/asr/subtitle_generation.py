"""Per-file subtitle-generation pipeline (ARC-015).

Product policy that used to live on ``SubtitleGenWorker`` — the status
mapping of the transcription outcome and the "no recognised speech is a
surfaced outcome, not a blank SRT" decision — lives here as
:func:`generate_subtitle_one`. The temp-WAV lifecycle and the extract → load →
transcribe orchestration live one level down in
:func:`anki_miner.services.asr.long_audio.transcribe_media`, which windows
tracks longer than 2 h so the old 6 h ceiling no longer applies. This module
returns a STRUCTURED :class:`SubtitleGenResult` (a status code plus the output
path) rather than a user-facing string; i18n stays in the GUI worker, which
maps each :class:`SubtitleGenStatus` back to a translated message. This keeps
the pipeline unit-testable without a QThread.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.services.asr.transcriber import Ct2ModelSession

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
    on_transcribe_start: Callable[[], None] | None = None,
    transcribe_progress_cb: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    ct2_model_session: Ct2ModelSession | None = None,
    language: str = "ja",
) -> SubtitleGenResult:
    """Transcribe one video to an SRT at *out_srt*.

    Pipeline: :func:`long_audio.transcribe_media` (extract to a temp WAV → load
    to float32 → transcribe, in 2 h windows for longer tracks; the temp WAV is
    always deleted) → write SRT. Empty transcription yields
    :attr:`SubtitleGenStatus.NO_SPEECH` (a surfaced outcome, never a blank
    SRT). A cancel checked after each stage yields
    :attr:`SubtitleGenStatus.CANCELLED`.

    Args:
        config: Frozen application config (ASR model / device / roots).
        extractor: A ``MediaExtractorService`` (or stand-in) exposing
            ``extract_full_audio`` and ``extract_audio_window``.
        video_path: Source video to transcribe.
        out_srt: Destination SRT path (its parent is created if missing).
        on_extract_start: Called once right before extraction begins (the worker
            uses it to emit an "Extracting audio" progress line).
        on_transcribe_start: Called once after the audio is loaded, right before
            transcription begins (silence mask, model construction, first decode
            window). Skipped when a cancel landed during extraction or load.
        transcribe_progress_cb: Forwarded to the transcriber as its
            ``progress_cb`` (called with a 0.0–1.0 fraction).
        cancel_event: Cooperative cancel, forwarded to extractor + transcriber.
        ct2_model_session: Optional queue-owned faster-whisper model state.
        language: ISO code from ``LanguageProfile.asr_language``, forwarded to the
            transcriber; the default keeps every existing caller on Japanese.

    Unexpected exceptions propagate to the caller (the worker isolates them
    per-file); the temp-WAV cleanup is guaranteed by ``transcribe_media``.
    """
    # Lazy imports keep the heavy ASR / media-extractor modules off this module's
    # import path and let tests patch them at their canonical location.
    from anki_miner.services.asr import long_audio, srt_writer

    # --- Stages 1–3: extract → load → transcribe (windowed past 2 h) ---
    result = long_audio.transcribe_media(
        config,
        extractor,
        video_path,
        cancel_event=cancel_event,
        progress_cb=transcribe_progress_cb,
        on_extract_start=on_extract_start,
        on_transcribe_start=on_transcribe_start,
        ct2_model_session=ct2_model_session,
        language=language,
    )
    if result.status is long_audio.LongAudioStatus.CANCELLED:
        return SubtitleGenResult(SubtitleGenStatus.CANCELLED)
    if result.status is long_audio.LongAudioStatus.EXTRACTION_FAILED:
        return SubtitleGenResult(SubtitleGenStatus.EXTRACTION_FAILED)

    # No recognised speech: surface it rather than writing a blank SRT and
    # reporting a clean "Done". Empty audio is already rejected upstream by
    # the extractor; this catches silence / music-only tracks.
    if not srt_writer.writable_segments(result.segments):
        logger.info("subtitle_generation: no speech detected in %s", video_path)
        return SubtitleGenResult(SubtitleGenStatus.NO_SPEECH)

    # --- Stage 4: write SRT ---
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    srt_writer.segments_to_srt(result.segments, out_srt)
    return SubtitleGenResult(SubtitleGenStatus.SUCCESS, out_srt=out_srt)

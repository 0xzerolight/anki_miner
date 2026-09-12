"""Transcribe media of any length in bounded windows.

``wav_to_float32`` refuses a track past ``_MAX_ASR_DURATION_S`` (6 h) because a
whole-file float32 buffer is ~230 MB per hour. A 7 h audiobook is a normal
input for Utilities → Audiobook Sync, and the same file used to fail in
Utilities → Generate. This module decodes ``WINDOW_SECONDS`` at a time through
:meth:`MediaExtractorService.extract_audio_window`, cuts each window at the
quietest 100 ms inside ``±CUT_SEARCH_SECONDS`` of the nominal edge (so a cut
lands in a pause, not mid-word), transcribes the window with the caller's
``Ct2ModelSession`` (the model loads once), and offsets every segment by the
window start.

A track that fits in one window — or whose duration ffprobe cannot report —
takes the pre-existing whole-file path, so every normal episode and film is
transcribed exactly as before, ceiling included.
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
from typing import TYPE_CHECKING, Any

from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.services.asr.transcriber import Ct2ModelSession

logger = logging.getLogger(__name__)

#: Nominal window length. 2 h keeps every episode, drama and feature film on
#: the whole-file path (the user-visible behaviour of Utilities → Generate is
#: unchanged for them) and bounds the resident float32 buffer at ~460 MB —
#: the same buffer a 2 h film already costs today. Only longer tracks window.
WINDOW_SECONDS = 2 * 60 * 60
#: Half-width of the band around the nominal edge searched for the quietest frame.
CUT_SEARCH_SECONDS = 20
_CUT_FRAME_MS = 100
#: A window this much shorter than requested means the audio ended (see the loop).
_SHORT_WINDOW_TOLERANCE_S = 1.0
#: Less audio than this left after a window is not worth a window of its own.
_MIN_REMAINDER_S = 1.0


class LongAudioStatus(Enum):
    OK = auto()
    #: An extraction returned False for a non-cancel reason.
    EXTRACTION_FAILED = auto()
    #: The cancel event was set during extraction, load, or transcription.
    CANCELLED = auto()


@dataclass(frozen=True)
class LongAudioResult:
    status: LongAudioStatus
    #: Absolute ``(start_s, end_s, text)`` tuples in chronological order; empty unless OK.
    segments: list[tuple[float, float, str]]


def pick_cut_sample(
    samples: Any, sample_rate: int, *, target_s: float, search_s: float, frame_ms: int = _CUT_FRAME_MS
) -> int:
    """Sample index of the quietest ``frame_ms`` frame within ``target_s ± search_s``.

    Pure numpy; ties resolve to the earliest frame. The band is clamped to the
    array, so a short final window never indexes past its end.
    """
    import numpy as np  # noqa: PLC0415  (function-local like wav_to_float32)

    frame = max(1, int(sample_rate * frame_ms / 1000))
    lo = max(0, int((target_s - search_s) * sample_rate))
    hi = min(len(samples), int((target_s + search_s) * sample_rate))
    if hi - lo < frame:
        return min(len(samples), max(0, int(target_s * sample_rate)))
    usable = (hi - lo) // frame * frame
    band = samples[lo : lo + usable].reshape(-1, frame).astype(np.float32)
    rms = np.sqrt(np.mean(band * band, axis=1))
    return lo + int(np.argmin(rms)) * frame


def _default_probe(config: AnkiMinerConfig) -> Callable[[Path], float | None]:
    ffprobe = resolve_ffprobe(config)
    return lambda path: get_media_duration_seconds(path, ffprobe_cmd=ffprobe)


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def transcribe_media(
    config: AnkiMinerConfig,
    extractor: Any,
    media_path: Path,
    *,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
    on_extract_start: Callable[[], None] | None = None,
    on_transcribe_start: Callable[[], None] | None = None,
    ct2_model_session: Ct2ModelSession | None = None,
    language: str = "ja",
    probe_duration: Callable[[Path], float | None] | None = None,
) -> LongAudioResult:
    """Extract → load → transcribe *media_path*, windowed when it is longer than one window.

    ``extractor`` exposes ``extract_full_audio`` and ``extract_audio_window``
    (a ``MediaExtractorService`` or a stand-in). ``probe_duration`` defaults
    to ffprobe through the app's resolver; ``None`` from it means "unknown"
    and selects the whole-file path. ``on_extract_start`` / ``on_transcribe_start``
    each fire once, before the first window's extraction / decode.
    """
    from anki_miner.services.asr import transcriber
    from anki_miner.services.media_extractor import wav_to_float32

    duration = (probe_duration or _default_probe(config))(media_path)
    windowed = duration is not None and duration > WINDOW_SECONDS + CUT_SEARCH_SECONDS

    temp_dir = config.media_temp_folder
    temp_dir.mkdir(parents=True, exist_ok=True)

    transcribe_kwargs: dict[str, Any] = {}
    if ct2_model_session is not None:
        transcribe_kwargs["ct2_model_session"] = ct2_model_session
    if language != "ja":  # omit-when-ja keeps the historical call shape
        transcribe_kwargs["language"] = language

    reported = 0.0  # last fraction handed to progress_cb

    def _decode(
        audio: Any, sample_rate: int, seconds: float, window_start: float, total: float
    ) -> list[tuple[float, float, str]]:
        def _scaled(fraction: float) -> None:
            nonlocal reported
            if progress_cb is not None:
                reported = min((window_start + fraction * seconds) / total, 1.0) if total > 0 else fraction
                progress_cb(reported)

        return transcriber.transcribe(
            audio,
            model_name=config.asr_model,
            models_root=config.asr_models_root,
            sample_rate=sample_rate,
            duration_s=seconds,
            cancel_event=cancel_event,
            progress_cb=_scaled if progress_cb is not None else None,
            device=config.asr_device,
            cuda_libs_root=config.cuda_libs_root,
            onnx_pack_root=config.onnx_pack_root,
            **transcribe_kwargs,
        )

    if on_extract_start is not None:
        on_extract_start()

    segments: list[tuple[float, float, str]] = []
    started_transcribe = False
    pos = 0.0
    final = not windowed
    while True:
        fd, tmp_str = tempfile.mkstemp(prefix="asr_", suffix=".wav", dir=temp_dir)
        os.close(fd)
        tmp_wav = Path(tmp_str)
        try:
            if windowed:
                assert duration is not None
                want = min(float(WINDOW_SECONDS + CUT_SEARCH_SECONDS), duration - pos)
                ok = extractor.extract_audio_window(
                    media_path, tmp_wav, start_s=pos, duration_s=want, cancel_event=cancel_event
                )
            else:
                want = 0.0
                ok = extractor.extract_full_audio(media_path, tmp_wav, cancel_event=cancel_event)
            if _is_cancelled(cancel_event):
                return LongAudioResult(LongAudioStatus.CANCELLED, [])
            if not ok:
                return LongAudioResult(LongAudioStatus.EXTRACTION_FAILED, [])

            samples, sample_rate, got_s = wav_to_float32(tmp_wav)
            if _is_cancelled(cancel_event):
                return LongAudioResult(LongAudioStatus.CANCELLED, [])

            if windowed:
                assert duration is not None
                # A window shorter than requested is the audio's real end — the
                # container duration overstated it (MKV with longer video, padded
                # m4b). Treat it as final rather than asking ffmpeg for a window
                # past EOF, which the zero-frame guard would report as a failure.
                final = got_s < want - _SHORT_WINDOW_TOLERANCE_S or pos + got_s >= duration - _MIN_REMAINDER_S
                if final:
                    chunk, chunk_s = samples, got_s
                else:
                    cut = pick_cut_sample(samples, sample_rate, target_s=WINDOW_SECONDS, search_s=CUT_SEARCH_SECONDS)
                    chunk, chunk_s = samples[:cut], cut / sample_rate
                total_s = duration
            else:
                # Whole-file path: the historical call shape — the array goes to
                # the transcriber untouched (test fakes may pass a non-array).
                chunk, chunk_s, total_s = samples, got_s, got_s

            if not started_transcribe:
                started_transcribe = True
                if on_transcribe_start is not None:
                    on_transcribe_start()
            for start, end, text in _decode(chunk, sample_rate, chunk_s, pos, total_s):
                segments.append((start + pos, end + pos, text))
            if _is_cancelled(cancel_event):
                return LongAudioResult(LongAudioStatus.CANCELLED, [])
            pos += chunk_s
        finally:
            try:
                if tmp_wav.exists():
                    tmp_wav.unlink()
            except OSError:
                logger.warning("long_audio: could not delete temp WAV %s", tmp_wav)

        if final:
            break

    # A container whose duration overstated the audio leaves the scaled
    # fraction short of 1.0; close it. The whole-file path already reported
    # 1.0 from the transcriber and gets no duplicate.
    if progress_cb is not None and reported < 1.0:
        progress_cb(1.0)
    log_summary(
        logger,
        "Long audio transcribe done",
        file=media_path,
        windowed=windowed,
        seconds=f"{pos:.0f}",
        segments=len(segments),
    )
    return LongAudioResult(LongAudioStatus.OK, segments)

"""What Whisper hears in one short clip, and how much of it is speech (``--api assess``).

For checking a card's audio: background music, two voices at once, a line in
another language, a bad cut. The clip is decoded as a transcription decodes it
(the same engine cascade, junk and non-speech rules and model session), and
the decode's :class:`~anki_miner.services.asr.transcriber.DecodeDetail` adds
Whisper's own language detection, every segment with its numbers and the
Silero speech regions. The RMS level inside and outside those regions is
measured here. No transcript cache and no sentence re-splitting: a clip is a
line or two.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.services.asr.transcriber import Ct2ModelSession, DecodedSegment

#: The level given to silence. The quietest step of 16-bit audio is about -96 dBFS.
LEVEL_FLOOR_DB = -100.0


@dataclass(frozen=True)
class ClipAssessment:
    """One clip's numbers; the speech fields are None when the VAD cannot run."""

    engine: str | None
    device: str | None
    #: Seconds of audio decoded.
    duration: float
    #: The segments a transcription keeps, joined by a space.
    text: str
    detected_language: str | None
    detected_language_probability: float | None
    #: Whisper's probability for the language it was told to listen for.
    language_probability: float | None
    #: Share of the clip inside the speech regions.
    speech_ratio: float | None
    #: RMS level in dBFS inside, then outside, the speech regions; None when that side is empty.
    speech_level_db: float | None
    background_level_db: float | None
    speech: list[tuple[float, float]] | None
    segments: list[DecodedSegment]


def assess_clip(
    config: AnkiMinerConfig,
    extractor: Any,
    clip: Path,
    *,
    language: str,
    session: Ct2ModelSession,
    cancel_event: threading.Event | None = None,
) -> ClipAssessment | None:
    """*clip* decoded in *language* (the Whisper code); None when its audio cannot be extracted.

    *extractor* is a ``MediaExtractorService`` (or a stand-in with
    ``extract_full_audio``); its temp WAV goes to ``config.media_temp_folder``
    and is removed. A cancel can also end extraction with None, or the decode
    early: the caller checks *cancel_event* before using the result.
    """
    from anki_miner.services.asr import srt_writer, transcriber
    from anki_miner.services.media_extractor import wav_to_float32

    folder = config.media_temp_folder
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="assess_", suffix=".wav", dir=folder)
    os.close(fd)
    wav = Path(name)
    try:
        if not extractor.extract_full_audio(clip, wav, cancel_event=cancel_event):
            return None
        samples, rate, seconds = wav_to_float32(wav)
    finally:
        wav.unlink(missing_ok=True)

    detail = transcriber.DecodeDetail()
    kept = transcriber.transcribe(
        samples,
        model_name=config.asr_model,
        models_root=config.asr_models_root,
        sample_rate=rate,
        duration_s=seconds,
        cancel_event=cancel_event,
        device=config.asr_device,
        cuda_libs_root=config.cuda_libs_root,
        onnx_pack_root=config.onnx_pack_root,
        ct2_model_session=session,
        language=language,
        detail=detail,
    )
    ratio, speech_db, background_db = _speech_levels(samples, rate, detail.speech)
    return ClipAssessment(
        engine=detail.engine,
        device=detail.device,
        duration=seconds,
        text=" ".join(text for _start, _end, text in srt_writer.writable_segments(kept)),
        detected_language=detail.detected_language,
        detected_language_probability=detail.detected_language_probability,
        language_probability=detail.language_probabilities.get(language),
        speech_ratio=ratio,
        speech_level_db=speech_db,
        background_level_db=background_db,
        speech=detail.speech,
        segments=detail.segments,
    )


def _speech_levels(
    samples: Any, rate: int, speech: list[tuple[float, float]] | None
) -> tuple[float | None, float | None, float | None]:
    """``(speech_ratio, speech_level_db, background_level_db)`` of *samples* split by the *speech* regions."""
    import numpy as np  # noqa: PLC0415  (function-local like wav_to_float32)

    if speech is None or len(samples) == 0:
        return None, None, None
    inside = np.zeros(len(samples), dtype=bool)
    for start, end in speech:
        inside[max(0, round(start * rate)) : max(0, round(end * rate))] = True
    return float(inside.mean()), _level_db(samples[inside]), _level_db(samples[~inside])


def _level_db(part: Any) -> float | None:
    """RMS level of *part* in dBFS (1.0 is full scale), floored at :data:`LEVEL_FLOOR_DB`; None when empty."""
    import numpy as np  # noqa: PLC0415

    if len(part) == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(part, dtype=np.float64))))
    return max(LEVEL_FLOOR_DB, 20 * math.log10(rms)) if rms > 0 else LEVEL_FLOOR_DB

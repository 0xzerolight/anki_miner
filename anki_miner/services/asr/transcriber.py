"""ASR transcription — convert audio to timed text segments.

No top-level numpy or faster-whisper imports. numpy is a transitive dependency
of faster-whisper so a function-local ``import numpy`` is fine in Wave B
bodies, but this skeleton must be importable without either package installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from anki_miner.services.asr import _engine


def transcribe(
    audio,  # np.ndarray float32, mono 16 kHz — typed as Any to avoid top-level numpy import
    *,
    model_name: str,
    models_root: Path,
    sample_rate: int,
    duration_s: float,
    cancel_event=None,
    progress_cb: Callable[[float], None] | None = None,
) -> list[tuple[float, float, str]]:
    """Transcribe *audio* using the specified faster-whisper model.

    Args:
        audio: Raw audio as a float32 numpy array, mono, sampled at *sample_rate* Hz.
        model_name: Model identifier (see ``model_manager.KNOWN_MODELS``).
        models_root: Directory containing downloaded model weights.
        sample_rate: Sampling rate of *audio* in Hz (typically 16 000).
        duration_s: Total duration of the audio in seconds; used for progress
            normalisation.
        cancel_event: Optional ``threading.Event``; transcription is aborted
            cooperatively when set.
        progress_cb: Optional callback receiving a float in ``[0.0, 1.0]``
            representing transcription progress.

    Returns:
        A list of ``(start_s, end_s, text)`` tuples in chronological order.
    """
    # sample_rate is part of the public interface for callers that need it
    # (e.g. resampling before passing the array); faster-whisper infers it
    # from the audio array's shape directly.
    del sample_rate

    if cancel_event is not None and cancel_event.is_set():
        if progress_cb is not None:
            progress_cb(1.0)
        return []

    whisper_model_cls = _engine.get_whisper_model_cls()
    cpu_threads = min(4, os.cpu_count() or 4)
    model = whisper_model_cls(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        download_root=models_root,
        local_files_only=True,
    )

    # vad_filter stays False on purpose: Silero VAD needs onnxruntime, which the
    # PyInstaller bundle deliberately excludes (anki_miner.spec) to save ~100 MB.
    # Enabling it here would crash only in the bundled build. Trade-off: on long
    # silence/music stretches Whisper can hallucinate repeated text.
    segments_iter, _info = model.transcribe(audio, language="ja", vad_filter=False)

    results: list[tuple[float, float, str]] = []
    for seg in segments_iter:
        if cancel_event is not None and cancel_event.is_set():
            break
        results.append((seg.start, seg.end, seg.text.strip()))
        if progress_cb is not None and duration_s > 0:
            progress_cb(min(seg.end / duration_s, 1.0))

    if progress_cb is not None:
        progress_cb(1.0)

    return results

"""ASR transcription — convert audio to timed text segments.

No top-level numpy or faster-whisper imports. numpy is a transitive dependency
of faster-whisper so a function-local ``import numpy`` is fine in Wave B
bodies, but this skeleton must be importable without either package installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


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

    Raises:
        NotImplementedError: Wave B fills the body.
    """
    raise NotImplementedError

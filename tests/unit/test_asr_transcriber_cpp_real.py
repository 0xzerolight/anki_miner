"""Real-CPU integration test for the whisper.cpp (_cpp_segments) path.

Unlike test_asr_transcriber.py, this exercises pywhispercpp for real: it obtains
the tiny model (auto-downloaded to the platformdirs cache on first run) and runs
a short synthesized clip through ``transcriber._cpp_segments``.

The load-bearing assertion is UNITS: pywhispercpp ``Segment.t0/.t1`` are
CENTISECONDS, so ``_cpp_segments`` must divide by 100 to yield SECONDS. A
centisecond/units bug would make ``end`` ~100x too large (a 2 s clip would report
~200 s) — this is what the test pins. Cancel/progress behaviour stays covered by
the mocked tests in test_asr_transcriber.py.

Gated to the asr CI job (needs the pywhispercpp wheel + numpy). Skips ONLY when
the tiny model genuinely cannot be obtained (e.g. offline first run).
"""

from __future__ import annotations

import pytest

from anki_miner.services.asr import _engine, transcriber

# Live model load + decode; needs the pywhispercpp CPU wheel and numpy.
pytestmark = pytest.mark.asr

_CLIP_SECONDS = 2.0
_SAMPLE_RATE = 16000


def _synthesize_voiced_clip():
    """Return ~2 s of formant-like, syllable-modulated mono 16 kHz float32 audio.

    Pure silence/tones are unreliable (zero or hallucinated segments); a voiced
    fundamental + a couple of harmonics under a 4 Hz syllable envelope reliably
    drives whisper.cpp's tiny model to emit at least one timed segment, which is
    all the units assertion needs.
    """
    import numpy as np

    t = np.linspace(0, _CLIP_SECONDS, int(_SAMPLE_RATE * _CLIP_SECONDS), endpoint=False)
    f0 = 120.0
    sig = np.sin(2 * np.pi * f0 * t) + 0.5 * np.sin(2 * np.pi * 3 * f0 * t) + 0.3 * np.sin(2 * np.pi * 5 * f0 * t)
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 4 * t))  # 4 Hz syllable rate
    return (sig * envelope * 0.3).astype(np.float32)


def _load_tiny_model():
    """Construct a real pywhispercpp ``Model("tiny")`` or skip if unobtainable."""
    try:
        model_cls = _engine.get_whisper_cpp_model_cls()
    except ImportError as exc:  # pragma: no cover — pywhispercpp absent
        pytest.skip(f"pywhispercpp not installed: {exc}")
    try:
        # "tiny" is a known alias pywhispercpp auto-downloads into its cache.
        return model_cls("tiny")
    except Exception as exc:  # noqa: BLE001 — offline / download failure → skip, not fail
        pytest.skip(f"tiny whisper.cpp model could not be obtained: {exc}")


def test_cpp_segments_real_units_in_seconds():
    """A real tiny-model decode yields segments with start<end, IN SECONDS.

    The load-bearing units assertion: a 2 s clip must report ``end`` on the order
    of a couple of seconds, NOT a couple of hundred (the centisecond bug).
    """
    audio = _synthesize_voiced_clip()
    model = _load_tiny_model()

    segments = list(
        transcriber._cpp_segments(
            model,
            audio,
            duration_s=_CLIP_SECONDS,
            progress_cb=None,
            cancel_event=None,
            decode_params={"language": "ja", "no_context": True},
        )
    )

    assert segments, "tiny model produced no segments for the synthesized clip"
    for seg in segments:
        # start/end must be ordered and inside the clip (in SECONDS).
        assert seg.start < seg.end, f"segment not ordered: start={seg.start} end={seg.end}"
        assert seg.start >= 0.0
        # A units bug (centiseconds left undivided) makes end ~100x too large:
        # a 2 s clip would report ~200 s. Allow a small overrun past the clip.
        assert (
            seg.end <= _CLIP_SECONDS + 0.5
        ), f"segment end {seg.end}s far exceeds the {_CLIP_SECONDS}s clip (units bug?)"


def test_cpp_segments_real_progress_driven_live():
    """new_segment_callback fires progress live during a real decode, clamped to 1.0."""
    audio = _synthesize_voiced_clip()
    model = _load_tiny_model()

    progress: list[float] = []
    list(
        transcriber._cpp_segments(
            model,
            audio,
            duration_s=_CLIP_SECONDS,
            progress_cb=progress.append,
            cancel_event=None,
            decode_params={"language": "ja", "no_context": True},
        )
    )

    assert progress, "no live progress was emitted during the real decode"
    assert all(0.0 <= v <= 1.0 for v in progress)

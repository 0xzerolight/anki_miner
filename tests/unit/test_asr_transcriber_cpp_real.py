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

Marked ``network``: obtaining the tiny model is a real HTTP download on a cold
cache, which the socket tripwire (tests/_network_tripwire.py) otherwise records
and fails at teardown — flaky on whether the runner already has the model. The
marker suppresses the tripwire for these tests; when the download genuinely
fails (offline), ``_load_tiny_model`` still catches it and skips, so the tests
are never red for a missing model — they run when it is obtainable, skip when
not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.services.asr import _engine, transcriber

# Live model load + decode; needs the pywhispercpp CPU wheel and numpy. ``network``
# lets the cold-cache model download through the socket tripwire (see docstring).
pytestmark = [pytest.mark.asr, pytest.mark.network]

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


_GGML_MAGIC = b"lmgg"  # uint32 0x67676d6c little-endian at offset 0 of every ggml .bin
_MIN_TINY_BYTES = 1_000_000  # real ggml-tiny.bin is ~78 MB; an HTML error page is <1 KB


def _tiny_model_file_is_valid() -> bool:
    """True if the cached ggml-tiny.bin exists and looks like a real ggml model.

    pywhispercpp's downloader does not check the HTTP status (no
    ``raise_for_status``), so a Hugging Face 5xx writes the HTML error page to
    the cache as ggml-tiny.bin — which then SEGFAULTS whisper.cpp's native
    loader, killing the xdist worker (three consecutive CI reds, 2026-07-16).
    A skip guard in Python is the only place to catch it: validate magic+size
    before any native code sees the file, and purge garbage so the next run
    re-downloads.
    """
    from pywhispercpp.constants import MODELS_DIR

    path = Path(MODELS_DIR) / "ggml-tiny.bin"
    if not path.is_file():
        return False
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if magic == _GGML_MAGIC and path.stat().st_size >= _MIN_TINY_BYTES:
            return True
        path.unlink(missing_ok=True)  # corrupt download — purge so a later run retries
    except OSError:
        return False
    return False


def _load_tiny_model():
    """Construct a real pywhispercpp ``Model("tiny")`` or skip if unobtainable."""
    try:
        model_cls = _engine.get_whisper_cpp_model_cls()
    except ImportError as exc:  # pragma: no cover — pywhispercpp absent
        pytest.skip(f"pywhispercpp not installed: {exc}")
    try:
        from pywhispercpp.utils import download_model

        # Download explicitly (a no-op when cached) so the file can be validated
        # BEFORE model construction hands it to native code.
        download_model("tiny")
    except Exception as exc:  # noqa: BLE001 — offline / download failure → skip, not fail
        pytest.skip(f"tiny whisper.cpp model could not be obtained: {exc}")
    if not _tiny_model_file_is_valid():
        pytest.skip("cached ggml-tiny.bin is missing or corrupt (bad download purged)")
    try:
        # "tiny" resolves to the validated cache file.
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

"""Tests for anki_miner.services.asr.transcriber.

faster-whisper is intentionally NOT installed. All tests monkeypatch the
_engine seam so no real model loading or network calls occur.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from anki_miner.services.asr import _engine, transcriber

# Requires numpy (transitive asr dep via faster-whisper); gated to the asr CI job.
pytestmark = pytest.mark.asr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_segment(start: float, end: float, text: str) -> SimpleNamespace:
    """Create a fake faster-whisper segment namespace."""
    return SimpleNamespace(start=start, end=end, text=text)


def fake_model_cls_factory(segments):
    """Return a fake WhisperModel class that yields *segments* on transcribe()."""

    class FakeModel:
        def __init__(self, model_name, *, device, compute_type, cpu_threads, download_root, local_files_only):
            self.model_name = model_name
            self.device = device
            self.compute_type = compute_type
            self.cpu_threads = cpu_threads
            self.download_root = download_root
            self.local_files_only = local_files_only

        def transcribe(self, audio, *, language, vad_filter):
            return iter(segments), SimpleNamespace(language=language)

    return FakeModel


# ---------------------------------------------------------------------------
# Basic transcription — returns correct tuples
# ---------------------------------------------------------------------------


def test_transcribe_returns_list_of_tuples(monkeypatch, tmp_path):
    """transcribe() must return a list of (start, end, text) tuples."""
    import numpy as np

    segs = [
        make_segment(0.0, 1.5, " hello "),
        make_segment(1.5, 3.0, " world"),
    ]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=3.0,
    )

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == (0.0, 1.5, "hello")
    assert result[1] == (1.5, 3.0, "world")


def test_transcribe_strips_whitespace_from_text(monkeypatch, tmp_path):
    """Text in returned tuples must be stripped of leading/trailing whitespace."""
    import numpy as np

    segs = [make_segment(0.0, 2.0, "  spaces  ")]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=2.0,
    )
    assert result[0][2] == "spaces"


def test_transcribe_empty_segments(monkeypatch, tmp_path):
    """transcribe() with no segments returns an empty list."""
    import numpy as np

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory([]))

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=0.0,
    )
    assert result == []


# ---------------------------------------------------------------------------
# Model construction parameters
# ---------------------------------------------------------------------------


def test_transcribe_builds_model_with_correct_params(monkeypatch, tmp_path):
    """transcribe() must pass correct params to WhisperModel constructor."""
    import numpy as np

    constructed = {}

    class CapturingModel:
        def __init__(self, model_name, *, device, compute_type, cpu_threads, download_root, local_files_only):
            constructed.update(
                model_name=model_name,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                download_root=download_root,
                local_files_only=local_files_only,
            )

        def transcribe(self, audio, *, language, vad_filter):
            return iter([]), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CapturingModel)

    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="large-v3",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
    )

    assert constructed["model_name"] == "large-v3"
    assert constructed["device"] == "cpu"
    assert constructed["compute_type"] == "int8"
    assert constructed["download_root"] == tmp_path
    assert constructed["local_files_only"] is True
    # cpu_threads must be min(4, os.cpu_count() or 4)
    import os

    expected_threads = min(4, os.cpu_count() or 4)
    assert constructed["cpu_threads"] == expected_threads


def test_transcribe_calls_model_transcribe_with_correct_params(monkeypatch, tmp_path):
    """transcribe() must call model.transcribe(audio, language='ja', vad_filter=False)."""
    import numpy as np

    call_kwargs: dict = {}
    audio = np.zeros(16000, dtype=np.float32)

    class CapturingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, received_audio, *, language, vad_filter):
            call_kwargs["language"] = language
            call_kwargs["vad_filter"] = vad_filter
            call_kwargs["audio_is_same"] = received_audio is audio
            return iter([]), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CapturingModel)

    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
    )

    assert call_kwargs["language"] == "ja"
    assert call_kwargs["vad_filter"] is False
    assert call_kwargs["audio_is_same"] is True


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


def test_transcribe_progress_cb_called_per_segment(monkeypatch, tmp_path):
    """progress_cb must be called once per segment with end/duration_s."""
    import numpy as np

    segs = [
        make_segment(0.0, 1.0, "a"),
        make_segment(1.0, 2.0, "b"),
        make_segment(2.0, 3.0, "c"),
    ]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    progress_values: list[float] = []
    audio = np.zeros(48000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=3.0,
        progress_cb=progress_values.append,
    )

    # Expect per-segment calls + final 1.0
    # At minimum: 3 segment calls + 1 final = 4
    assert len(progress_values) >= 4
    assert progress_values[-1] == pytest.approx(1.0)


def test_transcribe_progress_clamped_to_1(monkeypatch, tmp_path):
    """progress_cb value must never exceed 1.0 even if segment.end > duration_s."""
    import numpy as np

    segs = [make_segment(0.0, 999.0, "long")]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    progress_values: list[float] = []
    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        progress_cb=progress_values.append,
    )

    assert all(v <= 1.0 for v in progress_values)


def test_transcribe_progress_not_called_when_duration_zero(monkeypatch, tmp_path):
    """progress_cb must not emit per-segment values when duration_s == 0."""
    import numpy as np

    segs = [make_segment(0.0, 1.0, "a")]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    progress_values: list[float] = []
    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=0.0,
        progress_cb=progress_values.append,
    )

    # Only the forced final 1.0 is emitted
    assert progress_values == [pytest.approx(1.0)]


def test_transcribe_final_progress_always_emitted(monkeypatch, tmp_path):
    """progress_cb(1.0) must be called after the loop even with no segments."""
    import numpy as np

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory([]))

    progress_values: list[float] = []
    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=5.0,
        progress_cb=progress_values.append,
    )

    assert 1.0 in progress_values


# ---------------------------------------------------------------------------
# Cancel behaviour
# ---------------------------------------------------------------------------


def test_transcribe_cancel_stops_early(monkeypatch, tmp_path):
    """Setting cancel_event during iteration must stop streaming early."""
    import numpy as np

    cancel = threading.Event()

    def generating_segments():
        yield make_segment(0.0, 1.0, "first")
        cancel.set()  # Set cancel after first segment
        yield make_segment(1.0, 2.0, "second")
        yield make_segment(2.0, 3.0, "third")

    class CancellingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, audio, *, language, vad_filter):
            return generating_segments(), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CancellingModel)

    audio = np.zeros(48000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=3.0,
        cancel_event=cancel,
    )

    # "first" is appended before cancel is checked; "second" and "third" must not appear
    assert result == [(0.0, 1.0, "first")]


def test_transcribe_cancel_midloop_emits_final_progress(monkeypatch, tmp_path):
    """progress_cb must receive 1.0 even when cancel fires mid-loop."""
    import numpy as np

    cancel = threading.Event()

    def generating_segments():
        yield make_segment(0.0, 1.0, "first")
        cancel.set()  # set cancel after yielding first segment
        yield make_segment(1.0, 2.0, "second")

    class CancellingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, audio, *, language, vad_filter):
            return generating_segments(), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CancellingModel)

    progress_values: list[float] = []
    audio = np.zeros(48000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=2.0,
        cancel_event=cancel,
        progress_cb=progress_values.append,
    )

    assert progress_values[-1] == pytest.approx(1.0)


def test_transcribe_cancel_preset_returns_empty(monkeypatch, tmp_path):
    """If cancel_event is already set before transcribe(), return empty list."""
    import numpy as np

    cancel = threading.Event()
    cancel.set()

    segs = [make_segment(0.0, 1.0, "text")]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        cancel_event=cancel,
    )

    assert result == []

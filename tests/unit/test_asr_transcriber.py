"""Tests for anki_miner.services.asr.transcriber.

faster-whisper is intentionally NOT installed. All tests monkeypatch the
_engine seam so no real model loading or network calls occur.
"""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

from anki_miner.services.asr import _engine, transcriber

# Requires numpy (transitive asr dep via faster-whisper); gated to the asr CI job.
pytestmark = pytest.mark.asr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_segment(
    start: float,
    end: float,
    text: str,
    *,
    avg_logprob: float = 0.0,
    compression_ratio: float = 0.0,
    no_speech_prob: float = 0.0,
) -> SimpleNamespace:
    """Create a fake faster-whisper segment namespace.

    The confidence fields default to values that pass every drop filter (so
    existing tests keep each segment); override them to exercise dropping.
    """
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        avg_logprob=avg_logprob,
        compression_ratio=compression_ratio,
        no_speech_prob=no_speech_prob,
    )


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

        def transcribe(self, audio, **kwargs):
            return iter(segments), SimpleNamespace(language=kwargs.get("language"))

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

        def transcribe(self, audio, **kwargs):
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
    """transcribe() must pass the anti-hallucination decode flags with VAD OFF + greedy."""
    import numpy as np

    call_kwargs: dict = {}
    audio = np.zeros(16000, dtype=np.float32)

    class CapturingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, received_audio, **kwargs):
            call_kwargs.update(kwargs)
            call_kwargs["audio_is_same"] = received_audio is audio
            return iter([]), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CapturingModel)
    # Isolate from the real Silero mask (the drop-filter is tested separately).
    monkeypatch.setattr(transcriber, "_speech_mask", lambda audio, onnx_pack_root: None)

    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
    )

    assert call_kwargs["language"] == "ja"
    assert call_kwargs["condition_on_previous_text"] is False
    assert call_kwargs["word_timestamps"] is True  # required: unlocks hallucination_silence_threshold
    assert call_kwargs["hallucination_silence_threshold"] == 2.0
    assert call_kwargs["temperature"] == 0.0  # deterministic re-mining
    assert call_kwargs["vad_filter"] is False  # DELIBERATELY off (spans + fragments bug)
    assert call_kwargs["audio_is_same"] is True


def _capture_transcribe_kwargs(monkeypatch, tmp_path, *, vad_available: bool) -> dict:
    """Run transcribe() with a capturing fake model; return the decode kwargs."""
    import numpy as np

    call_kwargs: dict = {}

    class CapturingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, received_audio, **kwargs):
            call_kwargs.update(kwargs)
            return iter([]), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CapturingModel)
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: vad_available)
    monkeypatch.setattr(transcriber, "_speech_mask", lambda audio, onnx_pack_root: None)
    transcriber.transcribe(
        np.zeros(16000, dtype=np.float32),
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
    )
    return call_kwargs


def test_transcribe_vad_filter_always_off(monkeypatch, tmp_path):
    """vad_filter is False regardless of onnxruntime availability (the VAD is now a
    post-decode speech mask, not faster-whisper's timeline-mangling vad_filter)."""
    assert _capture_transcribe_kwargs(monkeypatch, tmp_path, vad_available=True)["vad_filter"] is False
    assert _capture_transcribe_kwargs(monkeypatch, tmp_path, vad_available=False)["vad_filter"] is False


# ---------------------------------------------------------------------------
# Junk-segment post-filter
# ---------------------------------------------------------------------------


def test_transcribe_drops_junk_segments(monkeypatch, tmp_path):
    """Segments with degenerate compression or very low confidence are dropped."""
    import numpy as np

    segs = [
        make_segment(0.0, 1.0, "clean"),  # passes (defaults)
        make_segment(1.0, 2.0, "あらあらあら", compression_ratio=3.5),  # repetition loop
        make_segment(2.0, 3.0, "garbage", avg_logprob=-1.4),  # low-confidence salad
        make_segment(3.0, 4.0, "keep"),  # passes
    ]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))

    audio = np.zeros(64000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=4.0,
    )

    assert result == [(0.0, 1.0, "clean"), (3.0, 4.0, "keep")]


def test_is_junk_segment_boundaries():
    """_is_junk_segment uses Whisper's own thresholds (2.4 / -1.0)."""
    # Exactly on the boundary is kept; just past it is dropped.
    assert transcriber._is_junk_segment(make_segment(0, 1, "x", compression_ratio=2.4)) is False
    assert transcriber._is_junk_segment(make_segment(0, 1, "x", compression_ratio=2.41)) is True
    assert transcriber._is_junk_segment(make_segment(0, 1, "x", avg_logprob=-1.0)) is False
    assert transcriber._is_junk_segment(make_segment(0, 1, "x", avg_logprob=-1.01)) is True
    # A segment object lacking the fields is never treated as junk (test fakes).
    assert transcriber._is_junk_segment(SimpleNamespace(start=0, end=1, text="x")) is False


# ---------------------------------------------------------------------------
# vad_available — onnxruntime detection + pack sys.path injection
# ---------------------------------------------------------------------------


def test_vad_available_true_when_onnxruntime_importable(monkeypatch):
    """vad_available is True when onnxruntime resolves via find_spec."""
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "onnxruntime" else None)
    assert transcriber.vad_available() is True


def test_vad_available_false_when_missing_and_no_pack(monkeypatch):
    """vad_available is False when onnxruntime is absent and no pack is provided."""
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert transcriber.vad_available(None) is False


def test_vad_available_injects_pack_then_imports(monkeypatch, tmp_path):
    """A pack dir holding onnxruntime/ is added to sys.path and then resolves."""
    import importlib.util
    import sys

    pack_root = tmp_path / "onnx_pack"
    (pack_root / "onnxruntime").mkdir(parents=True)
    (pack_root / "onnxruntime" / "__init__.py").write_text("")

    # find_spec resolves onnxruntime only once the pack dir is on sys.path.
    def fake_find_spec(name):
        if name == "onnxruntime" and str(pack_root) in sys.path:
            return object()
        return None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    inserted = str(pack_root) not in sys.path
    try:
        assert transcriber.vad_available(pack_root) is True
        assert str(pack_root) in sys.path
    finally:
        if inserted and str(pack_root) in sys.path:
            sys.path.remove(str(pack_root))


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

        def transcribe(self, audio, **kwargs):
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

        def transcribe(self, audio, **kwargs):
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


# ---------------------------------------------------------------------------
# Device selection (CPU / CUDA / auto) with CPU fallback
# ---------------------------------------------------------------------------


def _recording_model_cls(constructed: list[dict], *, cuda_raises: bool = False):
    """Fake WhisperModel that records every constructor kwarg dict.

    Accepts arbitrary kwargs so both the CPU build (with cpu_threads) and the
    CUDA build (without) are captured. When *cuda_raises* is set, constructing
    with device='cuda' raises to exercise the CPU fallback path.
    """

    class RecordingModel:
        def __init__(self, model_name, **kwargs):
            kwargs["model_name"] = model_name
            constructed.append(kwargs)
            if cuda_raises and kwargs.get("device") == "cuda":
                raise RuntimeError("cuDNN not found")

        def transcribe(self, audio, **kwargs):
            return iter([]), SimpleNamespace(language=kwargs.get("language"))

    return RecordingModel


def _fake_ctranslate2(monkeypatch, device_count: int):
    """Install a fake ctranslate2 module reporting *device_count* GPUs."""
    import sys

    fake = SimpleNamespace(get_cuda_device_count=lambda: device_count)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)


def test_device_cpu_never_queries_cuda(monkeypatch, tmp_path):
    """device='cpu' builds a CPU model and never imports/queries ctranslate2."""
    import sys

    import numpy as np

    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed))
    # If cuda were queried, importing this poisoned module would blow up.
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: (_ for _ in ()).throw(AssertionError("queried cuda"))),
    )

    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        device="cpu",
    )

    assert len(constructed) == 1
    assert constructed[0]["device"] == "cpu"
    assert constructed[0]["compute_type"] == "int8"


def test_device_auto_with_gpu_builds_cuda(monkeypatch, tmp_path):
    """device='auto' + GPU present + success → builds a CUDA float16 model."""
    import numpy as np

    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed))
    monkeypatch.setattr(transcriber, "_preload_cuda_libs", lambda root: None)
    _fake_ctranslate2(monkeypatch, device_count=1)

    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="large-v3",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        device="auto",
    )

    assert len(constructed) == 1
    assert constructed[0]["device"] == "cuda"
    assert constructed[0]["compute_type"] == "float16"


def test_device_auto_cuda_failure_falls_back_to_cpu(monkeypatch, tmp_path):
    """device='auto' + GPU present + CUDA construction raises → CPU fallback, no exception."""
    import numpy as np

    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed, cuda_raises=True))
    monkeypatch.setattr(transcriber, "_preload_cuda_libs", lambda root: None)
    _fake_ctranslate2(monkeypatch, device_count=1)

    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(
        audio,
        model_name="large-v3",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        device="auto",
    )

    # First attempt cuda (raises), second attempt cpu (succeeds).
    assert [c["device"] for c in constructed] == ["cuda", "cpu"]
    assert constructed[-1]["compute_type"] == "int8"
    assert result == []


def test_device_cuda_no_gpu_falls_back_to_cpu_with_warning(monkeypatch, tmp_path, caplog):
    """device='cuda' but no GPU → CPU build plus a warning."""
    import numpy as np

    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed))
    _fake_ctranslate2(monkeypatch, device_count=0)

    audio = np.zeros(16000, dtype=np.float32)
    with caplog.at_level(logging.WARNING, logger=transcriber.__name__):
        transcriber.transcribe(
            audio,
            model_name="small",
            models_root=tmp_path,
            sample_rate=16000,
            duration_s=1.0,
            device="cuda",
        )

    assert len(constructed) == 1
    assert constructed[0]["device"] == "cpu"
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_device_cuda_deferred_inference_failure_falls_back_to_cpu(monkeypatch, tmp_path, caplog):
    """CUDA build succeeds but the first decode raises (ctranslate2 validates the
    compute-type/cuDNN kernels lazily) → rebuild on CPU, no exception escapes, and
    the CPU segments are returned intact."""
    import numpy as np

    constructed: list[dict] = []

    class DeferredFailModel:
        def __init__(self, model_name, **kwargs):
            kwargs["model_name"] = model_name
            constructed.append(kwargs)
            self._device = kwargs.get("device")

        def transcribe(self, audio, **kwargs):
            if self._device == "cuda":

                def _boom():
                    raise RuntimeError("cuDNN kernel launch failed")
                    yield  # pragma: no cover  (makes _boom a generator)

                return _boom(), SimpleNamespace(language="ja")
            return iter([make_segment(0.0, 1.0, "ok")]), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: DeferredFailModel)
    monkeypatch.setattr(transcriber, "_preload_cuda_libs", lambda root: None)
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: False)
    _fake_ctranslate2(monkeypatch, device_count=1)

    audio = np.zeros(16000, dtype=np.float32)
    with caplog.at_level(logging.WARNING, logger=transcriber.__name__):
        result = transcriber.transcribe(
            audio,
            model_name="large-v3",
            models_root=tmp_path,
            sample_rate=16000,
            duration_s=1.0,
            device="auto",
        )

    # First built cuda (decode raises), then rebuilt cpu (succeeds).
    assert [c["device"] for c in constructed] == ["cuda", "cpu"]
    assert result == [(0.0, 1.0, "ok")]
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_is_junk_segment_none_fields_do_not_crash():
    """A segment with present-but-None confidence fields is kept, not crashed on."""
    seg = SimpleNamespace(start=0.0, end=1.0, text="x", compression_ratio=None, avg_logprob=None)
    assert transcriber._is_junk_segment(seg) is False


def test_cuda_device_count_failure_treated_as_no_gpu(monkeypatch, tmp_path):
    """If get_cuda_device_count() raises, treat as 0 GPUs and build CPU (auto, no error)."""
    import sys

    import numpy as np

    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed))
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: (_ for _ in ()).throw(RuntimeError("driver error"))),
    )

    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        device="auto",
    )

    assert len(constructed) == 1
    assert constructed[0]["device"] == "cpu"


# ---------------------------------------------------------------------------
# _preload_cuda_libs — best-effort, never raises
# ---------------------------------------------------------------------------


def test_preload_cuda_libs_none_never_raises():
    """_preload_cuda_libs(None) must be a no-op that never raises."""
    transcriber._preload_cuda_libs(None)


def test_preload_cuda_libs_empty_dir_never_raises(monkeypatch, tmp_path):
    """_preload_cuda_libs with a libs dir that has no libs must never raise."""
    import ctypes

    # Guard: even if some path were found, CDLL is mocked so nothing is dlopened.
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: None)
    transcriber._preload_cuda_libs(tmp_path)


def test_preload_cuda_libs_loads_pack_libs(monkeypatch, tmp_path):
    """_preload_cuda_libs CDLL-loads matching pack libs found under cuda_libs_root."""
    import ctypes

    cudnn_lib = tmp_path / "cudnn" / "lib" / "libcudnn.so.9"
    cublas_lib = tmp_path / "cublas" / "lib" / "libcublas.so.12"
    cudnn_lib.parent.mkdir(parents=True)
    cublas_lib.parent.mkdir(parents=True)
    cudnn_lib.write_bytes(b"")
    cublas_lib.write_bytes(b"")

    loaded: list[str] = []
    monkeypatch.setattr(ctypes, "CDLL", lambda path, **kw: loaded.append(str(path)))
    # Make pip-package fallback a guaranteed no-op so only pack libs count.
    import sys

    monkeypatch.setitem(sys.modules, "nvidia", SimpleNamespace())

    transcriber._preload_cuda_libs(tmp_path)

    assert str(cudnn_lib) in loaded
    assert str(cublas_lib) in loaded


def test_preload_cuda_libs_cdll_error_never_raises(monkeypatch, tmp_path):
    """A CDLL failure on one lib must not propagate out of _preload_cuda_libs."""
    import ctypes

    lib = tmp_path / "cudnn" / "lib" / "libcudnn.so.9"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"")

    def _boom(*a, **kw):
        raise OSError("cannot load")

    monkeypatch.setattr(ctypes, "CDLL", _boom)
    transcriber._preload_cuda_libs(tmp_path)


# ---------------------------------------------------------------------------
# whisper.cpp (pywhispercpp) engine path — device→engine cascade + cpp loop
# ---------------------------------------------------------------------------


def make_cpp_segment(t0: int, t1: int, text: str, probability: float) -> SimpleNamespace:
    """Create a fake pywhispercpp Segment (t0/t1 in CENTISECONDS, geom-mean prob)."""
    return SimpleNamespace(t0=t0, t1=t1, text=text, probability=probability)


def fake_cpp_model_cls_factory(segments, *, raises: bool = False, constructed: list | None = None):
    """Return a fake pywhispercpp Model class.

    transcribe() fires *new_segment_callback* live per fake Segment (so progress
    is driven exactly like the real engine) and then returns the materialized
    list. Set *raises* to make transcribe() blow up after construction (exercises
    the whole-attempt try/except → CT2 CPU fallback). Each Model path is recorded
    in *constructed* when given.
    """

    class FakeCppModel:
        def __init__(self, model_path):
            self.model_path = model_path
            if constructed is not None:
                constructed.append(model_path)

        def transcribe(
            self,
            audio,
            *,
            new_segment_callback=None,
            abort_callback=None,
            extract_probability=False,
            **params,
        ):
            self.last_params = params
            self.extract_probability = extract_probability
            if raises:
                raise RuntimeError("vulkan device lost")
            for seg in segments:
                if abort_callback is not None and abort_callback():
                    break
                if new_segment_callback is not None:
                    new_segment_callback(seg)
            return list(segments)

    return FakeCppModel


def _wire_cpp(
    monkeypatch,
    *,
    cuda=0,
    vulkan=0,
    cpp_available=True,
    cpp_segments=None,
    cpp_raises=False,
    cpp_constructed=None,
):
    """Monkeypatch the _engine cascade seams + the whisper.cpp Model class.

    Returns the CT2 ``constructed`` list so callers can assert whether a CT2 CPU
    model was built (the always-safe fallback).
    """
    monkeypatch.setattr(_engine, "cuda_device_count", lambda: cuda)
    monkeypatch.setattr(_engine, "vulkan_device_count", lambda: vulkan)
    monkeypatch.setattr(_engine, "whisper_cpp_available", lambda: cpp_available)
    # _resolve_model consults the in-module _cuda_device_count for the CT2 'auto'
    # path; keep it in lockstep with the cascade's cuda count so CT2 fallbacks are
    # deterministic regardless of any real GPU on the host.
    monkeypatch.setattr(transcriber, "_cuda_device_count", lambda: cuda)
    monkeypatch.setattr(
        _engine,
        "get_whisper_cpp_model_cls",
        lambda: fake_cpp_model_cls_factory(
            cpp_segments or [],
            raises=cpp_raises,
            constructed=cpp_constructed,
        ),
    )
    # The CT2 branch shares _resolve_model; capture its constructions too.
    ct2_constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(ct2_constructed))
    # ggml acoustic + VAD present by default (overridable by the test).
    monkeypatch.setattr(transcriber, "_cpp_ggml_present", lambda model_name, models_root: True)
    monkeypatch.setattr(transcriber.ggml_model_installer, "is_vad_downloaded", lambda models_root: False)
    return ct2_constructed


def _run_cpp_transcribe(monkeypatch, tmp_path, *, device, duration_s=1.0, **kw):
    """Call transcriber.transcribe() with a zeroed audio array under *device*."""
    import numpy as np

    audio = np.zeros(16000, dtype=np.float32)
    return transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=duration_s,
        device=device,
        **kw,
    )


# --- cascade matrix: which engine/device wins for each device × gpu combo ----


def test_cascade_cpu_uses_ct2_cpu(monkeypatch, tmp_path):
    """device='cpu' never touches the whisper.cpp seam — pure CT2 CPU."""
    monkeypatch.setattr(
        _engine,
        "whisper_cpp_available",
        lambda: (_ for _ in ()).throw(AssertionError("queried cpp on cpu device")),
    )
    monkeypatch.setattr(
        _engine,
        "vulkan_device_count",
        lambda: (_ for _ in ()).throw(AssertionError("queried vulkan on cpu device")),
    )
    ct2: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(ct2))

    _run_cpp_transcribe(monkeypatch, tmp_path, device="cpu")
    assert [c["device"] for c in ct2] == ["cpu"]


def test_cascade_cuda_uses_ct2_cuda(monkeypatch, tmp_path):
    """device='cuda' + a CUDA GPU → CT2 CUDA, the whisper.cpp seam untouched."""
    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed))
    monkeypatch.setattr(transcriber, "_preload_cuda_libs", lambda root: None)
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: False)
    monkeypatch.setattr(
        _engine,
        "whisper_cpp_available",
        lambda: (_ for _ in ()).throw(AssertionError("queried cpp on cuda device")),
    )
    _fake_ctranslate2(monkeypatch, device_count=1)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="cuda")
    assert constructed[0]["device"] == "cuda"


def test_cascade_vulkan_available_with_device_uses_cpp(monkeypatch, tmp_path):
    """device='vulkan' + cpp available + a Vulkan device → whisper.cpp engine."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_available=True,
        cpp_segments=[make_cpp_segment(0, 100, "a", 0.9)],
        cpp_constructed=cpp_constructed,
    )

    result = _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")
    assert len(cpp_constructed) == 1  # whisper.cpp Model built
    assert ct2 == []  # CT2 never used
    assert result == [(0.0, 1.0, "a")]


def test_cascade_vulkan_unavailable_falls_back_to_ct2_cpu(monkeypatch, tmp_path, caplog):
    """device='vulkan' but whisper_cpp_available()==False → CT2 CPU + a log."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(monkeypatch, vulkan=1, cpp_available=False, cpp_constructed=cpp_constructed)

    with caplog.at_level(logging.INFO, logger=transcriber.__name__):
        _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")
    assert cpp_constructed == []
    assert [c["device"] for c in ct2] == ["cpu"]
    assert caplog.records  # logged the fallback


def test_cascade_vulkan_no_device_falls_back_to_ct2_cpu(monkeypatch, tmp_path):
    """device='vulkan', cpp available, but vulkan_device_count()==0 → CT2 CPU."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(monkeypatch, vulkan=0, cpp_available=True, cpp_constructed=cpp_constructed)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")
    assert cpp_constructed == []
    assert [c["device"] for c in ct2] == ["cpu"]


def test_cascade_vulkan_unavailable_with_cuda_uses_ct2_cuda(monkeypatch, tmp_path):
    """device='vulkan' but no whisper.cpp backend, WITH a CUDA GPU present →
    falls back to CT2 'auto' which builds CUDA (salvages GPU, not forced CPU)."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(monkeypatch, cuda=1, vulkan=1, cpp_available=False, cpp_constructed=cpp_constructed)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")
    assert cpp_constructed == []
    assert [c["device"] for c in ct2] == ["cuda"]


def test_cascade_auto_prefers_cuda(monkeypatch, tmp_path):
    """device='auto' + a CUDA GPU → CT2 CUDA wins over Vulkan."""
    constructed: list[dict] = []
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _recording_model_cls(constructed))
    monkeypatch.setattr(transcriber, "_preload_cuda_libs", lambda root: None)
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: False)
    monkeypatch.setattr(_engine, "cuda_device_count", lambda: 1)
    # cpp present + vulkan present, but cuda must win and short-circuit cpp.
    monkeypatch.setattr(
        _engine,
        "whisper_cpp_available",
        lambda: (_ for _ in ()).throw(AssertionError("queried cpp when cuda present")),
    )
    _fake_ctranslate2(monkeypatch, device_count=1)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="auto")
    assert constructed[0]["device"] == "cuda"


def test_cascade_auto_no_cuda_uses_cpp_when_vulkan(monkeypatch, tmp_path):
    """device='auto', no CUDA, cpp available + a Vulkan device → whisper.cpp."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(
        monkeypatch,
        cuda=0,
        vulkan=1,
        cpp_available=True,
        cpp_segments=[make_cpp_segment(0, 100, "x", 0.8)],
        cpp_constructed=cpp_constructed,
    )

    result = _run_cpp_transcribe(monkeypatch, tmp_path, device="auto")
    assert len(cpp_constructed) == 1
    assert ct2 == []
    assert result == [(0.0, 1.0, "x")]


def test_cascade_auto_no_gpu_uses_ct2_cpu(monkeypatch, tmp_path):
    """device='auto', no CUDA, no Vulkan → CT2 CPU."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(monkeypatch, cuda=0, vulkan=0, cpp_available=True, cpp_constructed=cpp_constructed)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="auto")
    assert cpp_constructed == []
    assert [c["device"] for c in ct2] == ["cpu"]


def test_cascade_auto_cpp_absent_with_vulkan_device_uses_ct2_cpu(monkeypatch, tmp_path):
    """device='auto', no CUDA, a Vulkan device but whisper.cpp ABSENT → CT2 CPU.

    Covers the cpp-absent leg of the auto-arm guard
    ``not (whisper_cpp_available() and vulkan_device_count() > 0)`` — distinct
    from the no-Vulkan-device leg above (cpp present, device count 0).
    """
    cpp_constructed: list = []
    ct2 = _wire_cpp(monkeypatch, cuda=0, vulkan=1, cpp_available=False, cpp_constructed=cpp_constructed)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="auto")
    assert cpp_constructed == []
    assert [c["device"] for c in ct2] == ["cpu"]


# --- whisper.cpp engine behaviour -------------------------------------------


def test_cpp_segment_units_centiseconds_to_seconds(monkeypatch, tmp_path):
    """Segment.t0=150 / t1=320 CENTISECONDS must become start=1.5 / end=3.2 SECONDS."""
    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[make_cpp_segment(150, 320, " yo ", 0.9)],
    )
    result = _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan", duration_s=4.0)
    assert ct2 == []
    assert result == [(1.5, 3.2, "yo")]


def test_cpp_low_probability_segment_dropped_normal_kept(monkeypatch, tmp_path):
    """A cpp segment with probability below the floor is dropped; a normal one is kept."""
    floor = transcriber._MIN_CPP_SEGMENT_PROBABILITY
    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[
            make_cpp_segment(0, 100, "garbage", floor - 0.05),  # below floor → dropped
            make_cpp_segment(100, 200, "keep", 0.9),  # above floor → kept
        ],
    )
    result = _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan", duration_s=2.0)
    assert ct2 == []
    assert result == [(1.0, 2.0, "keep")]


def test_cpp_nan_probability_segment_kept(monkeypatch, tmp_path):
    """A NaN/None probability is treated as unknown (kept), never dropped."""
    import math

    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[
            make_cpp_segment(0, 100, "nan", float("nan")),
            make_cpp_segment(100, 200, "none", None),
        ],
    )
    result = _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan", duration_s=2.0)
    assert ct2 == []
    assert result == [(0.0, 1.0, "nan"), (1.0, 2.0, "none")]
    assert not math.isnan(0.0)  # sanity: floor predicate must not have crashed on NaN


def test_cpp_progress_driven_live_from_callback(monkeypatch, tmp_path):
    """Live progress comes from new_segment_callback, not from iterating the result."""
    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[
            make_cpp_segment(0, 100, "a", 0.9),
            make_cpp_segment(100, 200, "b", 0.9),
        ],
    )
    progress: list[float] = []
    _run_cpp_transcribe(
        monkeypatch,
        tmp_path,
        device="vulkan",
        duration_s=2.0,
        progress_cb=progress.append,
    )
    assert ct2 == []
    # Two live callbacks (t1/100/dur = 0.5, 1.0) + final 1.0.
    assert progress[0] == pytest.approx(0.5)
    assert all(v <= 1.0 for v in progress)
    assert progress[-1] == pytest.approx(1.0)


def test_cpp_cancel_via_abort_callback(monkeypatch, tmp_path):
    """A preset cancel_event makes abort_callback() True → no segments decoded."""
    cancel = threading.Event()
    cancel.set()
    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[make_cpp_segment(0, 100, "should-not-appear", 0.9)],
    )
    result = _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan", cancel_event=cancel)
    # Preset cancel short-circuits before any engine work.
    assert result == []
    assert ct2 == []


def test_cpp_cancel_midflight_aborts_remaining_segments(monkeypatch, tmp_path):
    """Setting cancel_event mid-decode → abort_callback stops the in-flight decode.

    Mirrors the CT2 mid-iteration cancel test (test_transcribe_cancel_stops_early):
    cancel fires INSIDE the first new_segment_callback (via progress_cb), so the
    fake model's abort_callback() returns True before the SECOND segment is
    emitted — exercising the _should_abort -> abort_callback in-flight path. The
    first segment is emitted (live progress fires); the second must not be.
    """
    cancel = threading.Event()
    emitted_progress: list[float] = []

    def _progress(value: float) -> None:
        emitted_progress.append(value)
        cancel.set()  # cancel during the FIRST live segment callback

    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[
            make_cpp_segment(0, 100, "first", 0.9),
            make_cpp_segment(100, 200, "second", 0.9),
        ],
    )
    _run_cpp_transcribe(
        monkeypatch,
        tmp_path,
        device="vulkan",
        duration_s=2.0,
        cancel_event=cancel,
        progress_cb=_progress,
    )
    assert ct2 == []  # stayed on the cpp engine (no CT2 fallback)
    # Exactly one LIVE segment callback fired (the first seg's 0.5), then the
    # second seg's abort_callback short-circuited before its callback. The final
    # progress_cb(1.0) still fires after the loop, so 0.5 then 1.0.
    assert emitted_progress[0] == pytest.approx(0.5)
    assert pytest.approx(1.0) not in emitted_progress[:-1]
    assert emitted_progress[-1] == pytest.approx(1.0)
    # The second segment's live progress (1.0 mid-loop) was never emitted, proving
    # abort_callback stopped the decode in-flight: only the first + the final fired.
    assert len(emitted_progress) == 2


def test_cpp_transcribe_raises_falls_back_to_ct2_cpu(monkeypatch, tmp_path, caplog):
    """If the whisper.cpp transcribe() RAISES, fall back to a full CT2 CPU re-decode."""
    cpp_constructed: list = []
    _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_raises=True,
        cpp_constructed=cpp_constructed,
    )
    # Give the CT2 CPU fallback something to return so we prove it ran (this
    # overrides the recording CT2 model _wire_cpp installed, so we assert via the
    # returned segments rather than the captured CT2 constructions).
    monkeypatch.setattr(
        _engine,
        "get_whisper_model_cls",
        lambda: fake_model_cls_factory([make_segment(0.0, 1.0, "ct2-cpu")]),
    )

    with caplog.at_level(logging.WARNING, logger=transcriber.__name__):
        result = _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")

    assert len(cpp_constructed) == 1  # cpp was attempted
    assert result == [(0.0, 1.0, "ct2-cpu")]  # CT2 CPU re-decode returned
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_cpp_missing_ggml_model_falls_back_to_ct2_cpu(monkeypatch, tmp_path, caplog):
    """A missing ggml acoustic file → CT2 CPU, the cpp Model never constructed."""
    cpp_constructed: list = []
    ct2 = _wire_cpp(
        monkeypatch,
        vulkan=1,
        cpp_segments=[make_cpp_segment(0, 100, "a", 0.9)],
        cpp_constructed=cpp_constructed,
    )
    monkeypatch.setattr(transcriber, "_cpp_ggml_present", lambda model_name, models_root: False)

    with caplog.at_level(logging.WARNING, logger=transcriber.__name__):
        _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")

    assert cpp_constructed == []  # never built the cpp model
    assert [c["device"] for c in ct2] == ["cpu"]
    assert caplog.records


def test_cpp_vad_enabled_only_when_present(monkeypatch, tmp_path):
    """decode_params enable VAD iff is_vad_downloaded(); else vad=False, never crash."""
    seen: dict = {}

    def _capturing_cls():
        class CapModel:
            def __init__(self, model_path):
                self.model_path = model_path

            def transcribe(
                self, audio, *, new_segment_callback=None, abort_callback=None, extract_probability=False, **params
            ):
                seen.update(params)
                seen["extract_probability"] = extract_probability
                return []

        return CapModel

    monkeypatch.setattr(_engine, "cuda_device_count", lambda: 0)
    monkeypatch.setattr(_engine, "vulkan_device_count", lambda: 1)
    monkeypatch.setattr(_engine, "whisper_cpp_available", lambda: True)
    monkeypatch.setattr(_engine, "get_whisper_cpp_model_cls", _capturing_cls)
    monkeypatch.setattr(transcriber, "_cpp_ggml_present", lambda model_name, models_root: True)

    vad_path = transcriber.ggml_model_installer.vad_model_path(tmp_path)
    monkeypatch.setattr(transcriber.ggml_model_installer, "is_vad_downloaded", lambda models_root: True)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")

    assert seen["language"] == "ja"
    assert seen["no_context"] is True
    assert seen["extract_probability"] is True
    assert seen["vad"] is True
    assert seen["vad_model_path"] == str(vad_path)


def test_cpp_vad_disabled_when_absent(monkeypatch, tmp_path):
    """When the VAD ggml is absent, vad=False and no vad_model_path is forced on."""
    seen: dict = {}

    def _capturing_cls():
        class CapModel:
            def __init__(self, model_path):
                self.model_path = model_path

            def transcribe(
                self, audio, *, new_segment_callback=None, abort_callback=None, extract_probability=False, **params
            ):
                seen.update(params)
                return []

        return CapModel

    monkeypatch.setattr(_engine, "cuda_device_count", lambda: 0)
    monkeypatch.setattr(_engine, "vulkan_device_count", lambda: 1)
    monkeypatch.setattr(_engine, "whisper_cpp_available", lambda: True)
    monkeypatch.setattr(_engine, "get_whisper_cpp_model_cls", _capturing_cls)
    monkeypatch.setattr(transcriber, "_cpp_ggml_present", lambda model_name, models_root: True)
    monkeypatch.setattr(transcriber.ggml_model_installer, "is_vad_downloaded", lambda models_root: False)

    _run_cpp_transcribe(monkeypatch, tmp_path, device="vulkan")

    assert seen["vad"] is False


def test_cpp_segments_generator_yields_ct2_duck_type(monkeypatch, tmp_path):
    """_cpp_segments yields SimpleNamespace(start,end,text,probability) the CT2 loop eats."""
    seg = make_cpp_segment(150, 320, "hi", 0.7)
    model = fake_cpp_model_cls_factory([seg])("/fake/model.bin")

    out = list(
        transcriber._cpp_segments(
            model,
            object(),
            duration_s=4.0,
            progress_cb=None,
            cancel_event=None,
            decode_params={"language": "ja"},
        )
    )
    assert len(out) == 1
    assert out[0].start == pytest.approx(1.5)
    assert out[0].end == pytest.approx(3.2)
    assert out[0].text == "hi"
    assert out[0].probability == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Non-speech drop (vad_filter-OFF CT2 path): speech mask + overlap + gates
# ---------------------------------------------------------------------------


def test_speech_overlap_math():
    """Fraction of a segment span covered by the speech mask (seconds)."""
    mask = [(1.0, 2.0), (5.0, 6.0)]
    assert transcriber._speech_overlap(1.0, 2.0, mask) == pytest.approx(1.0)  # full
    assert transcriber._speech_overlap(3.0, 4.0, mask) == 0.0  # none
    assert transcriber._speech_overlap(1.5, 2.5, mask) == pytest.approx(0.5)  # half
    assert transcriber._speech_overlap(2.0, 2.0, mask) == 0.0  # zero-width
    # [0.5,5.5] covers all of (1,2)=1.0 and half of (5,6)=0.5 → 1.5 / 5.0.
    assert transcriber._speech_overlap(0.5, 5.5, mask) == pytest.approx((1.0 + 0.5) / 5.0)


def test_speech_mask_converts_samples_to_seconds(monkeypatch):
    """B1: get_speech_timestamps returns SAMPLE indices; _speech_mask must ÷ 16000."""
    import faster_whisper.vad as fw_vad

    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: True)
    # 16000 samples = 1.0 s, 32000 = 2.0 s at the fixed 16 kHz rate.
    monkeypatch.setattr(fw_vad, "get_speech_timestamps", lambda audio, **kw: [{"start": 16000, "end": 32000}])

    mask = transcriber._speech_mask(object(), None)
    assert mask == [(1.0, 2.0)]
    # A segment at 1.0-2.0 s must therefore read as fully in-speech.
    assert transcriber._speech_overlap(1.0, 2.0, mask) == pytest.approx(1.0)


def test_speech_mask_none_when_vad_unavailable(monkeypatch):
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: False)
    assert transcriber._speech_mask(object(), None) is None


def test_speech_mask_none_on_vad_error(monkeypatch):
    import faster_whisper.vad as fw_vad

    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: True)

    def _boom(audio, **kw):
        raise RuntimeError("vad exploded")

    monkeypatch.setattr(fw_vad, "get_speech_timestamps", _boom)
    assert transcriber._speech_mask(object(), None) is None  # degrades, never raises


_IN = [(10.0, 40.0)]  # a speech region for the drop tests


def test_nonspeech_real_in_mask_kept():
    seg = make_segment(11.0, 13.0, "本物", no_speech_prob=0.15, compression_ratio=1.2)
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is False


def test_nonspeech_confident_prob_dropped_any_overlap():
    """no_speech_prob>=0.60 drops even inside the mask (VAD-false-positive hallucination)."""
    seg = make_segment(11.0, 13.0, "ご視聴", no_speech_prob=0.9, compression_ratio=0.8)
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is True


def test_nonspeech_long_out_of_speech_dropped():
    """A 7 s out-of-speech span (sung lyric) drops on duration, not nsp."""
    seg = make_segment(100.0, 107.0, "sung", no_speech_prob=0.30, compression_ratio=0.9)
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is True


def test_nonspeech_repetition_out_of_speech_dropped():
    seg = make_segment(100.0, 101.0, "ああ" * 50, no_speech_prob=0.30, compression_ratio=9.0)
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is True


def test_nonspeech_short_out_of_speech_low_prob_KEPT():
    """CARDINAL RULE: a short out-of-speech line the VAD MISSED, with low nsp, is a
    real interjection and must NEVER be dropped on the overlap verdict alone."""
    seg = make_segment(100.0, 102.0, "し、し、さあ!", no_speech_prob=0.09, compression_ratio=1.3)
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is False


def test_nonspeech_short_out_of_speech_midband_prob_KEPT():
    """A short out-of-speech line at nsp 0.30 (below the 0.60 confident cut) is kept:
    the overlap arm never fires on nsp alone (Path-1 closed)."""
    seg = make_segment(100.0, 102.0, "quiet real?", no_speech_prob=0.30, compression_ratio=1.2)
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is False


def test_nonspeech_co_resident_window_same_prob(monkeypatch):
    """B3: two segments sharing one window's (per-window) nsp — the in-mask one is
    KEPT, the out-of-mask long one is DROPPED. Overlap geometry, not nsp, decides."""
    real = make_segment(11.0, 13.0, "real", no_speech_prob=0.30, compression_ratio=1.1)
    junk = make_segment(100.0, 108.0, "sung", no_speech_prob=0.30, compression_ratio=1.1)
    assert transcriber._is_nonspeech_ct2_segment(real, _IN) is False
    assert transcriber._is_nonspeech_ct2_segment(junk, _IN) is True


def test_nonspeech_overlap_boundary():
    """overlap exactly at the 0.05 threshold is NOT < threshold → not dropped by overlap arm."""
    thr = transcriber._MIN_SPEECH_OVERLAP
    # segment [0,10] (dur 10s > 4s corroborates); mask covers [0, 10*thr] → overlap == thr.
    seg = make_segment(0.0, 10.0, "edge", no_speech_prob=0.30, compression_ratio=1.0)
    mask_at = [(0.0, 10.0 * thr)]
    assert transcriber._is_nonspeech_ct2_segment(seg, mask_at) is False  # overlap == thr, not < thr
    mask_below = [(0.0, 10.0 * thr - 0.01)]
    assert transcriber._is_nonspeech_ct2_segment(seg, mask_below) is True  # overlap < thr, dur corroborates


def test_nonspeech_prob_boundary():
    g = transcriber._CONFIDENT_NONSPEECH_PROB
    assert transcriber._is_nonspeech_ct2_segment(make_segment(11.0, 12.0, "x", no_speech_prob=g), _IN) is True
    below = make_segment(11.0, 12.0, "x", no_speech_prob=g - 0.01, compression_ratio=1.0)
    assert transcriber._is_nonspeech_ct2_segment(below, _IN) is False


def test_nonspeech_mask_none_skips_overlap_arm():
    """No mask (onnxruntime absent) → the overlap arm is skipped; only the confidence
    gates apply, so an out-of-speech sung line at nsp 0.30 LEAKS (documented degradation)."""
    seg = make_segment(100.0, 107.0, "sung", no_speech_prob=0.30, compression_ratio=0.9)
    assert transcriber._is_nonspeech_ct2_segment(seg, None) is False


def test_nonspeech_reuses_junk_gates():
    """compression_ratio / avg_logprob junk gates still fire (via _is_junk_segment)."""
    assert transcriber._is_nonspeech_ct2_segment(make_segment(11.0, 12.0, "x", compression_ratio=3.0), _IN) is True
    assert transcriber._is_nonspeech_ct2_segment(make_segment(11.0, 12.0, "x", avg_logprob=-2.0), _IN) is True


def test_nonspeech_no_crash_on_fieldless_segment():
    """getattr defaults keep the filter a no-op on a bare namespace (test fakes)."""
    seg = SimpleNamespace(start=11.0, end=12.0, text="x")
    assert transcriber._is_nonspeech_ct2_segment(seg, _IN) is False


def test_transcribe_drops_nonspeech_end_to_end(monkeypatch, tmp_path):
    """Full CT2 path: a long out-of-speech hallucination is dropped, real kept."""
    import numpy as np

    segs = [
        make_segment(11.0, 13.0, " 本物 ", no_speech_prob=0.15, compression_ratio=1.2),
        make_segment(100.0, 108.0, " sung ", no_speech_prob=0.30, compression_ratio=0.9),
    ]
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: fake_model_cls_factory(segs))
    monkeypatch.setattr(transcriber, "_speech_mask", lambda audio, onnx_pack_root: [(10.0, 40.0)])

    result = transcriber.transcribe(
        np.zeros(16000, dtype=np.float32),
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=110.0,
        device="cpu",
    )
    assert result == [(11.0, 13.0, "本物")]

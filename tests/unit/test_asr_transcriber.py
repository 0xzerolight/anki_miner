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

    The confidence fields default to values that pass the junk filter (so
    existing tests keep every segment); override them to exercise dropping.
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
    """transcribe() must pass the anti-hallucination decode flags + conditional VAD."""
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
    # Pin VAD availability so the assertion is deterministic regardless of whether
    # onnxruntime happens to be importable in the test environment.
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: True)

    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
    )

    assert call_kwargs["language"] == "ja"
    assert call_kwargs["condition_on_previous_text"] is False
    assert call_kwargs["word_timestamps"] is True
    assert call_kwargs["hallucination_silence_threshold"] == 2.0
    assert call_kwargs["vad_filter"] is True
    assert call_kwargs["audio_is_same"] is True


def test_transcribe_vad_filter_reflects_availability(monkeypatch, tmp_path):
    """vad_filter is False when onnxruntime/VAD is unavailable."""
    import numpy as np

    call_kwargs: dict = {}

    class CapturingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, received_audio, **kwargs):
            call_kwargs.update(kwargs)
            return iter([]), SimpleNamespace(language="ja")

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CapturingModel)
    monkeypatch.setattr(transcriber, "vad_available", lambda onnx_pack_root=None: False)

    audio = np.zeros(16000, dtype=np.float32)
    transcriber.transcribe(
        audio,
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
    )

    assert call_kwargs["vad_filter"] is False


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

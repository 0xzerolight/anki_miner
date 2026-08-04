"""Logging contracts for ASR backend selection and transcription."""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from anki_miner.services.asr import _engine, transcriber

pytestmark = pytest.mark.asr


def _fake_ct2_model_cls(segments):
    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            return iter(segments), SimpleNamespace(language=kwargs.get("language"))

    return FakeModel


def _run_cpu_transcription(monkeypatch, tmp_path, segments, *, device="cpu"):
    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: _fake_ct2_model_cls(segments))
    monkeypatch.setattr(transcriber, "_speech_mask", lambda audio, onnx_pack_root: None)
    return transcriber.transcribe(
        object(),
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=2.5,
        device=device,
    )


def test_failed_vulkan_backend_load_warns_with_backend(monkeypatch, tmp_path, caplog):
    import ctypes

    states = dict.fromkeys(_engine._GGML_BACKEND_STATES, _engine._BackendState.UNTRIED)
    monkeypatch.setattr(_engine, "_GGML_BACKEND_STATES", states)
    monkeypatch.setattr(_engine, "_find_ggml_vulkan_lib", lambda: tmp_path / "libggml-vulkan.so")
    monkeypatch.setattr(_engine, "_ggml_lib_search_dirs", lambda: [tmp_path])
    monkeypatch.setattr(_engine, "_find_ggml_core_lib", lambda _dirs: tmp_path / "libggml.so")

    def _fail_load(*args, **kwargs):
        raise OSError("broken Vulkan loader")

    monkeypatch.setattr(ctypes, "CDLL", _fail_load)

    with caplog.at_level(logging.DEBUG, logger="anki_miner.services.asr"):
        _engine.ensure_ggml_backends_loaded()
        _engine.ensure_ggml_backends_loaded()

    records = [r for r in caplog.records if r.getMessage().startswith("ASR backend load:")]
    assert len(records) == 1
    assert "backend=ggml-vulkan" in records[0].getMessage()
    assert "exc=OSError" in records[0].getMessage()
    assert records[0].levelno == logging.WARNING
    assert records[0].name == _engine.__name__
    assert all(state is _engine._BackendState.FAILED for state in states.values())


def test_absent_optional_accelerator_logs_debug_not_warning(monkeypatch, caplog):
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: (_ for _ in ()).throw(OSError("no CUDA runtime"))),
    )

    with caplog.at_level(logging.DEBUG, logger="anki_miner.services.asr"):
        assert _engine.cuda_device_count() == 0

    record = next(r for r in caplog.records if r.getMessage().startswith("ASR CUDA probe:"))
    assert "devices=0" in record.getMessage()
    assert record.levelno == logging.DEBUG
    assert record.name == _engine.__name__


def test_final_backend_selection_logged_once_at_info(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(_engine, "whisper_cpp_available", lambda: False)
    monkeypatch.setattr(transcriber, "_cuda_device_count", lambda: 0)

    with caplog.at_level(logging.DEBUG, logger="anki_miner.services.asr"):
        _run_cpu_transcription(monkeypatch, tmp_path, [], device="vulkan")

    records = [r for r in caplog.records if r.getMessage().startswith("ASR backend selected:")]
    assert len(records) == 1
    assert "backend=ctranslate2" in records[0].getMessage()
    assert records[0].levelno == logging.INFO
    assert records[0].name == transcriber.__name__
    fallback = next(r for r in caplog.records if r.getMessage().startswith("ASR backend fallback:"))
    assert "fallback=ctranslate2" in fallback.getMessage()


def test_transcription_summary_counts_segments_without_text(monkeypatch, tmp_path, caplog):
    private_text = "distinctive private transcript sentence"
    segment = SimpleNamespace(
        start=0.0,
        end=2.5,
        text=private_text,
        avg_logprob=0.0,
        compression_ratio=0.0,
        no_speech_prob=0.0,
    )

    with caplog.at_level(logging.DEBUG, logger="anki_miner.services.asr"):
        result = _run_cpu_transcription(monkeypatch, tmp_path, [segment])

    assert result == [(0.0, 2.5, private_text)]
    record = next(r for r in caplog.records if r.getMessage().startswith("ASR transcribe done:"))
    assert "segments=1" in record.getMessage()
    assert f"chars={len(private_text)}" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == transcriber.__name__
    assert all(private_text not in r.getMessage() for r in caplog.records)

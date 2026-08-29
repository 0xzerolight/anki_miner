"""ASR language threading: the profile's asr_language reaches every decoder."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.services.asr import _engine, transcriber

# numpy arrives with the asr extra, exactly as tests/unit/test_asr_transcriber.py.
pytestmark = pytest.mark.asr


def test_cpp_decode_params_default_is_ja(tmp_path):
    assert transcriber._cpp_decode_params(tmp_path)["language"] == "ja"


def test_cpp_decode_params_honours_language(tmp_path):
    assert transcriber._cpp_decode_params(tmp_path, language="ko")["language"] == "ko"


def _capture_ct2_kwargs(monkeypatch, tmp_path, **extra) -> dict:
    import numpy as np

    seen: dict = {}

    class CapturingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return iter([]), SimpleNamespace(language=kwargs.get("language"))

    monkeypatch.setattr(_engine, "get_whisper_model_cls", lambda: CapturingModel)
    # Same isolation the existing suite uses: no real Silero mask.
    monkeypatch.setattr(transcriber, "_speech_mask", lambda audio, onnx_pack_root: None)
    transcriber.transcribe(
        np.zeros(16000, dtype=np.float32),
        model_name="small",
        models_root=tmp_path,
        sample_rate=16000,
        duration_s=1.0,
        **extra,
    )
    return seen


def test_ct2_language_defaults_to_ja(monkeypatch, tmp_path):
    assert _capture_ct2_kwargs(monkeypatch, tmp_path)["language"] == "ja"


def test_ct2_language_is_threaded(monkeypatch, tmp_path):
    assert _capture_ct2_kwargs(monkeypatch, tmp_path, language="zh")["language"] == "zh"


def test_generate_subtitle_one_forwards_language(tmp_path, monkeypatch):
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services import media_extractor
    from anki_miner.services.asr import srt_writer, subtitle_generation

    seen: dict = {}

    class _Extractor:
        def extract_full_audio(self, video_file, out_wav, *, cancel_event=None):
            out_wav.write_bytes(b"")
            return True

    # subtitle_generation imports these lazily inside the function, at their
    # canonical location, precisely so tests patch them here (:90-93).
    monkeypatch.setattr(media_extractor, "wav_to_float32", lambda path: (object(), 16000, 2.0))
    monkeypatch.setattr(srt_writer, "segments_to_srt", lambda segs, out: out.write_text("SRT"))

    def _fake(audio, **kwargs):
        seen.update(kwargs)
        return [(0.0, 1.0, "가")]

    monkeypatch.setattr(transcriber, "transcribe", _fake)
    config = AnkiMinerConfig(asr_models_root=tmp_path / "m", media_temp_folder=tmp_path / "t")
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"")
    subtitle_generation.generate_subtitle_one(config, _Extractor(), video, tmp_path / "ep.srt", language="ko")
    assert seen["language"] == "ko"

"""Unit tests for :func:`anki_miner.services.asr.subtitle_generation.generate_subtitle_one`.

Exercises the per-file transcription policy directly (no QThread, no real ffmpeg /
ASR): the extractor is a stand-in and ``wav_to_float32`` / ``transcribe`` /
``segments_to_srt`` are patched at their canonical modules.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.asr.subtitle_generation import (
    SubtitleGenStatus,
    generate_subtitle_one,
)

# Importing the transcriber module transitively pulls numpy (faster-whisper).
pytestmark = pytest.mark.asr

_FAKE_SEGMENTS = [(0.0, 1.0, "こんにちは"), (1.0, 2.0, "世界")]


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        asr_model="large-v3",
        asr_models_root=tmp_path / "models",
        media_temp_folder=tmp_path / "temp",
    )


class _FakeExtractor:
    def __init__(self, *, fail: bool = False, cancel: bool = False) -> None:
        self._fail = fail
        self._cancel = cancel
        self.calls: list[dict] = []

    def extract_full_audio(self, video_file, out_wav, *, cancel_event=None):
        self.calls.append({"video_file": video_file, "out_wav": out_wav, "cancel_event": cancel_event})
        out_wav.write_bytes(b"")
        if self._cancel and cancel_event is not None:
            cancel_event.set()
        return not self._fail


def _patch_pipeline(monkeypatch, *, segments=None, transcribe_exc=None, cancel_on_transcribe=None, srt_exc=None):
    import anki_miner.services.asr.srt_writer as sw
    import anki_miner.services.asr.transcriber as t
    import anki_miner.services.media_extractor as me

    monkeypatch.setattr(me, "wav_to_float32", lambda path: (object(), 16000, 2.0))

    def _fake_transcribe(audio, *, progress_cb=None, cancel_event=None, **kwargs):
        if transcribe_exc is not None:
            raise transcribe_exc
        if cancel_on_transcribe is not None and cancel_event is not None:
            cancel_event.set()
        if progress_cb is not None:
            progress_cb(1.0)
        return _FAKE_SEGMENTS if segments is None else segments

    monkeypatch.setattr(t, "transcribe", _fake_transcribe)

    def _fake_write(segs, out_path):
        if srt_exc is not None:
            raise srt_exc
        out_path.write_text("SRT")

    monkeypatch.setattr(sw, "segments_to_srt", _fake_write)


def test_success_writes_srt_and_cleans_temp(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    out_srt = tmp_path / "ep01.srt"
    _patch_pipeline(monkeypatch)

    result = generate_subtitle_one(config, _FakeExtractor(), video, out_srt)

    assert result.status is SubtitleGenStatus.SUCCESS
    assert result.out_srt == out_srt
    assert out_srt.read_text() == "SRT"
    assert list(config.media_temp_folder.glob("asr_*.wav")) == []


def test_no_speech_surfaced_no_srt(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "silent.mkv"
    video.write_bytes(b"")
    out_srt = tmp_path / "silent.srt"
    _patch_pipeline(monkeypatch, segments=[])

    result = generate_subtitle_one(config, _FakeExtractor(), video, out_srt)

    assert result.status is SubtitleGenStatus.NO_SPEECH
    assert not out_srt.exists()


def test_extraction_failure(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    _patch_pipeline(monkeypatch)

    result = generate_subtitle_one(config, _FakeExtractor(fail=True), video, tmp_path / "ep01.srt")

    assert result.status is SubtitleGenStatus.EXTRACTION_FAILED


def test_cancel_after_extraction(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    _patch_pipeline(monkeypatch)
    cancel = threading.Event()

    result = generate_subtitle_one(
        config, _FakeExtractor(cancel=True), video, tmp_path / "ep01.srt", cancel_event=cancel
    )

    assert result.status is SubtitleGenStatus.CANCELLED


def test_cancel_during_transcribe(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    _patch_pipeline(monkeypatch, cancel_on_transcribe=True)
    cancel = threading.Event()

    result = generate_subtitle_one(config, _FakeExtractor(), video, tmp_path / "ep01.srt", cancel_event=cancel)

    assert result.status is SubtitleGenStatus.CANCELLED


def test_transcribe_exception_propagates_and_cleans_temp(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    _patch_pipeline(monkeypatch, transcribe_exc=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        generate_subtitle_one(config, _FakeExtractor(), video, tmp_path / "ep01.srt")

    assert list(config.media_temp_folder.glob("asr_*.wav")) == []


def test_on_extract_start_and_progress_callbacks(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    _patch_pipeline(monkeypatch)

    events: list[str] = []
    fractions: list[float] = []

    generate_subtitle_one(
        config,
        _FakeExtractor(),
        video,
        tmp_path / "ep01.srt",
        on_extract_start=lambda: events.append("extract"),
        transcribe_progress_cb=fractions.append,
    )

    assert events == ["extract"]
    assert fractions == [1.0]

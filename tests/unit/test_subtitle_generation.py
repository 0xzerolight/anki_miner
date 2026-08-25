"""Unit tests for :func:`anki_miner.services.asr.subtitle_generation.generate_subtitle_one`.

Exercises the per-file transcription policy directly (no QThread, no real ffmpeg /
ASR): the extractor is a stand-in and ``wav_to_float32`` / ``transcribe`` /
``segments_to_srt`` are patched at their canonical modules.
"""

from __future__ import annotations

import threading
import wave
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


def test_wav_normalization_reuses_the_owned_float_buffer(tmp_path, monkeypatch):
    """The float32 output is preallocated exactly once and scaled in place.

    Pins the peak-memory fix: ``wav_to_float32`` must never hold a whole-file
    int16 buffer and a separately-allocated whole-file float32 buffer at the
    same time. Tracking ``np.empty`` (not ``np.frombuffer``, which now only
    ever views small per-chunk reads) proves there is exactly one float32
    allocation, and that the returned array IS that allocation — i.e. the
    ``/= 32768.0`` scaling happened in place, not into a second buffer.
    """
    np = pytest.importorskip("numpy")

    from anki_miner.services.media_extractor import wav_to_float32

    pcm = np.array([-32768, -16384, 0, 16384, 32767], dtype=np.int16)
    expected = pcm.astype(np.float32)
    expected /= 32768.0
    wav_path = tmp_path / "samples.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm.tobytes())

    float_buffer_addresses: list[int] = []
    real_empty = np.empty

    def tracking_empty(*args, **kwargs):
        result = real_empty(*args, **kwargs)
        if result.dtype == np.float32:
            float_buffer_addresses.append(result.ctypes.data)
        return result

    with monkeypatch.context() as context:
        context.setattr(np, "empty", tracking_empty)
        samples, sample_rate, duration = wav_to_float32(wav_path)

    np.testing.assert_array_equal(samples, expected)
    assert samples.dtype == np.float32
    assert sample_rate == 16000
    assert duration == pytest.approx(len(pcm) / 16000)
    assert len(set(float_buffer_addresses)) == 1
    assert samples.ctypes.data == float_buffer_addresses[0]


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


def test_all_degenerate_segments_report_no_speech(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    video = tmp_path / "silent.mkv"
    video.write_bytes(b"")
    out_srt = tmp_path / "silent.srt"
    _patch_pipeline(monkeypatch, segments=[(0.0, 0.0, "text"), (1.0, 2.0, "  ")])

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

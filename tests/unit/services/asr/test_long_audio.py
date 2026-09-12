"""Windowed decode + transcribe for tracks past one window (services/asr/long_audio.py).

No ffmpeg, no ASR: the extractor is a stand-in that writes real 16 kHz WAVs,
``transcriber.transcribe`` is patched at its canonical module, and the duration
probe is injected. Marked ``asr`` because the fixtures build numpy arrays —
tests/unit/test_asr_marker_gating.py fails any unmarked test module that
imports numpy; health.sh runs the asr step as a hard gate, CI in test-asr.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.asr import long_audio
from anki_miner.services.asr.long_audio import LongAudioStatus, pick_cut_sample, transcribe_media

pytestmark = pytest.mark.asr

SR = 16000
# Shrunk windows so the fake WAVs stay tiny (a real 1820 s window is 58 MB on disk
# and 116 MB as float32 — too heavy under xdist). transcribe_media reads the two
# module globals at call time, so monkeypatching them is the whole seam.
W = 30
S = 2


@pytest.fixture(autouse=True)
def _small_windows(monkeypatch):
    monkeypatch.setattr(long_audio, "WINDOW_SECONDS", W)
    monkeypatch.setattr(long_audio, "CUT_SEARCH_SECONDS", S)


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(asr_models_root=tmp_path / "models", media_temp_folder=tmp_path / "temp")


def _write_wav(path: Path, seconds: float, *, quiet_at: float | None = None) -> None:
    n = int(seconds * SR)
    samples = np.full(n, 8000, dtype=np.int16)
    if quiet_at is not None:
        q = int(quiet_at * SR)
        samples[q : q + SR // 10] = 0  # one silent 100 ms frame
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(samples.tobytes())


class _FakeExtractor:
    """Records every call; writes a WAV of the requested length, clipped to the REAL audio length.

    ``total_s`` is the real audio; a window past it is clipped (like ffmpeg on
    a track whose container duration overstates the audio) and a window that
    starts at or past it yields an empty WAV → False (the zero-frame guard).
    """

    def __init__(self, total_s: float, *, quiet_at_in_window: float | None = None, fail_window: int | None = None):
        self.total_s = total_s
        self.quiet = quiet_at_in_window
        self.fail_window = fail_window
        self.full_calls: list[Path] = []
        self.window_calls: list[tuple[float, float]] = []

    def extract_full_audio(self, video_file, out_wav, *, cancel_event=None):
        self.full_calls.append(video_file)
        _write_wav(out_wav, self.total_s)
        return True

    def extract_audio_window(self, video_file, out_wav, *, start_s, duration_s, cancel_event=None):
        if self.fail_window is not None and len(self.window_calls) == self.fail_window:
            return False
        self.window_calls.append((start_s, duration_s))
        available = self.total_s - start_s
        if available <= 0:
            _write_wav(out_wav, 0.0)
            return False
        _write_wav(out_wav, min(duration_s, available), quiet_at=self.quiet)
        return True


def _patch_transcribe(monkeypatch, *, record: list, per_window=lambda d: [(0.0, 1.0, "a"), (d - 1.0, d, "b")]):
    import anki_miner.services.asr.transcriber as t

    def fake(audio, *, duration_s, progress_cb=None, cancel_event=None, ct2_model_session=None, **kwargs):
        record.append((len(audio), duration_s, ct2_model_session))
        if progress_cb is not None:
            progress_cb(1.0)
        return per_window(duration_s)

    monkeypatch.setattr(t, "transcribe", fake)


# --- pick_cut_sample -------------------------------------------------------


def test_pick_cut_sample_finds_the_quietest_frame_in_the_band():
    samples = np.full(SR * 60, 0.5, dtype=np.float32)
    samples[SR * 33 : SR * 33 + SR // 10] = 0.0  # silence at 33.0 s
    cut = pick_cut_sample(samples, SR, target_s=30.0, search_s=5.0)
    assert cut == SR * 33


def test_pick_cut_sample_clamps_the_band_to_the_array():
    samples = np.full(SR * 10, 0.5, dtype=np.float32)
    cut = pick_cut_sample(samples, SR, target_s=9.0, search_s=5.0)
    assert 0 <= cut <= len(samples)


def test_pick_cut_sample_is_deterministic_on_ties():
    samples = np.zeros(SR * 60, dtype=np.float32)
    assert pick_cut_sample(samples, SR, target_s=30.0, search_s=5.0) == SR * 25


# --- transcribe_media ---------------------------------------------------------


def test_short_track_uses_one_whole_file_extract(tmp_path, monkeypatch):
    record: list = []
    _patch_transcribe(monkeypatch, record=record)
    extractor = _FakeExtractor(total_s=20.0)  # < W + S → whole-file path
    result = transcribe_media(_make_config(tmp_path), extractor, tmp_path / "ep.mkv", probe_duration=lambda p: 20.0)
    assert result.status is LongAudioStatus.OK
    assert extractor.full_calls == [tmp_path / "ep.mkv"]
    assert extractor.window_calls == []
    assert result.segments == [(0.0, 1.0, "a"), (19.0, 20.0, "b")]


def test_unknown_duration_falls_back_to_whole_file(tmp_path, monkeypatch):
    record: list = []
    _patch_transcribe(monkeypatch, record=record)
    extractor = _FakeExtractor(total_s=30.0)
    result = transcribe_media(_make_config(tmp_path), extractor, tmp_path / "x.mp3", probe_duration=lambda p: None)
    assert result.status is LongAudioStatus.OK
    assert extractor.full_calls and not extractor.window_calls


def test_long_track_is_windowed_cut_at_silence_and_offset(tmp_path, monkeypatch):
    record: list = []
    _patch_transcribe(monkeypatch, record=record)
    total = 2.5 * W  # 75 s → 3 windows
    extractor = _FakeExtractor(total_s=total, quiet_at_in_window=W + 1.0)
    result = transcribe_media(_make_config(tmp_path), extractor, tmp_path / "book.m4b", probe_duration=lambda p: total)

    assert result.status is LongAudioStatus.OK
    starts = [s for s, _ in extractor.window_calls]
    assert starts[0] == 0.0
    # each cut landed on the silent frame 1 s past the nominal edge (band is ±S)
    assert starts[1] == pytest.approx(W + 1.0, abs=0.01)
    assert starts[2] == pytest.approx(2 * (W + 1.0), abs=0.01)
    assert len(extractor.window_calls) == 3
    # every non-final window asked for the search margin
    assert extractor.window_calls[0][1] == W + S
    # transcript of window 2 is offset by its start
    assert result.segments[2][0] == pytest.approx(starts[1], abs=0.01)
    assert result.segments[-1][1] == pytest.approx(total, abs=0.5)
    assert len(result.segments) == 6


def test_windows_share_one_model_session_and_report_progress(tmp_path, monkeypatch):
    record: list = []
    _patch_transcribe(monkeypatch, record=record)
    total = 2.5 * W  # 75 s → 3 windows
    extractor = _FakeExtractor(total_s=total)
    session = object()
    fractions: list[float] = []
    stages: list[str] = []
    transcribe_media(
        _make_config(tmp_path),
        extractor,
        tmp_path / "book.m4b",
        probe_duration=lambda p: total,
        ct2_model_session=session,  # type: ignore[arg-type]
        progress_cb=fractions.append,
        on_extract_start=lambda: stages.append("extract"),
        on_transcribe_start=lambda: stages.append("transcribe"),
    )
    assert all(entry[2] is session for entry in record)
    assert stages == ["extract", "transcribe"]  # each fires ONCE, before the first window
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_overstated_container_duration_ends_on_the_short_window(tmp_path, monkeypatch):
    """ffprobe's format=duration can exceed the audio (MKV with longer video, padded m4b).

    The window that comes back short is the last one: it is transcribed whole
    and the loop stops — never a request past EOF, never EXTRACTION_FAILED.
    """
    record: list = []
    _patch_transcribe(monkeypatch, record=record)
    real = 1.5 * W  # 45 s of audio
    claimed = real + 20.0  # container says 65 s
    extractor = _FakeExtractor(total_s=real)
    result = transcribe_media(
        _make_config(tmp_path), extractor, tmp_path / "book.mkv", probe_duration=lambda p: claimed
    )

    assert result.status is LongAudioStatus.OK
    assert len(extractor.window_calls) == 2
    assert result.segments[-1][1] == pytest.approx(real, abs=0.5)


def test_remaining_sliver_under_a_second_is_not_extracted(tmp_path, monkeypatch):
    record: list = []
    _patch_transcribe(monkeypatch, record=record)
    total = W + S + 0.4  # just past the windowing threshold, 0.4 s left after the first window
    extractor = _FakeExtractor(total_s=total, quiet_at_in_window=W)
    result = transcribe_media(_make_config(tmp_path), extractor, tmp_path / "book.m4b", probe_duration=lambda p: total)
    assert result.status is LongAudioStatus.OK
    assert len(extractor.window_calls) == 1


def test_window_extraction_failure_is_reported(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch, record=[])
    total = 2.5 * W  # 75 s → 3 windows
    extractor = _FakeExtractor(total_s=total, fail_window=1)
    result = transcribe_media(_make_config(tmp_path), extractor, tmp_path / "book.m4b", probe_duration=lambda p: total)
    assert result.status is LongAudioStatus.EXTRACTION_FAILED
    assert result.segments == []


def test_cancel_between_windows_is_reported(tmp_path, monkeypatch):
    event = threading.Event()
    import anki_miner.services.asr.transcriber as t

    def fake(audio, *, duration_s, progress_cb=None, cancel_event=None, **kwargs):
        cancel_event.set()
        return [(0.0, 1.0, "a")]

    monkeypatch.setattr(t, "transcribe", fake)
    total = 2.5 * W  # 75 s → 3 windows
    extractor = _FakeExtractor(total_s=total)
    result = transcribe_media(
        _make_config(tmp_path), extractor, tmp_path / "book.m4b", probe_duration=lambda p: total, cancel_event=event
    )
    assert result.status is LongAudioStatus.CANCELLED
    assert len(extractor.window_calls) == 1


def test_temp_wavs_are_removed(tmp_path, monkeypatch):
    _patch_transcribe(monkeypatch, record=[])
    total = 2.5 * W  # 75 s → 3 windows
    config = _make_config(tmp_path)
    transcribe_media(config, _FakeExtractor(total_s=total), tmp_path / "book.m4b", probe_duration=lambda p: total)
    assert list(config.media_temp_folder.glob("asr_*.wav")) == []


def test_default_probe_uses_the_resolved_ffprobe(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(long_audio, "resolve_ffprobe", lambda config: "/opt/ffprobe")

    def fake_probe(path, ffprobe_cmd="ffprobe"):
        seen["cmd"] = ffprobe_cmd
        return None  # "unknown" → whole-file path; a str here would blow up the `>` comparison

    monkeypatch.setattr(long_audio, "get_media_duration_seconds", fake_probe)
    _patch_transcribe(monkeypatch, record=[])
    transcribe_media(_make_config(tmp_path), _FakeExtractor(total_s=5.0), tmp_path / "x.mp3")
    assert seen["cmd"] == "/opt/ffprobe"

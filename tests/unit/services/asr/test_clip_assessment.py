"""clip_assessment over a fake extractor and a fake decode: text, language, speech share and levels."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.asr import clip_assessment, transcriber
from anki_miner.services.asr.transcriber import DecodeDetail, DecodedSegment

pytestmark = pytest.mark.asr

SR = 16000
#: 1 s of silence, then 1 s of a 0.5-amplitude tone (RMS 0.5/sqrt(2), about -9.03 dBFS).
TONE_DB = 20 * math.log10(0.5 / math.sqrt(2))


def _write_wav(path: Path) -> None:
    t = np.arange(SR) / SR
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    samples = np.concatenate([np.zeros(SR), tone])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())


class _Extractor:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.wavs: list[Path] = []

    def extract_full_audio(self, clip, out_wav, *, cancel_event=None):
        self.wavs.append(out_wav)
        if self.ok:
            _write_wav(out_wav)
        return self.ok


def _fake_decode(monkeypatch, *, speech, kept, segments=(), probabilities=None):
    calls: list[dict] = []

    def transcribe(samples, **kwargs):
        calls.append({"samples": samples, **kwargs})
        detail: DecodeDetail = kwargs["detail"]
        detail.engine, detail.device, detail.speech = "ctranslate2", "cuda", speech
        detail.segments = list(segments)
        detail.detected_language, detail.detected_language_probability = "en", 0.8
        detail.language_probabilities = probabilities if probabilities is not None else {"en": 0.8, "ja": 0.15}
        return kept

    monkeypatch.setattr(transcriber, "transcribe", transcribe)
    return calls


def _assess(tmp_path: Path, extractor: _Extractor, language: str = "ja"):
    config = AnkiMinerConfig(asr_models_root=tmp_path / "models", media_temp_folder=tmp_path / "temp")
    session = transcriber.Ct2ModelSession()
    return clip_assessment.assess_clip(config, extractor, tmp_path / "1.mp3", language=language, session=session)


def test_a_clip_gets_its_text_language_and_speech_levels(monkeypatch, tmp_path) -> None:
    segment = DecodedSegment(1.0, 2.0, "今日", True, avg_logprob=-0.2, no_speech_prob=0.1, compression_ratio=1.0)
    calls = _fake_decode(
        monkeypatch,
        speech=[(1.0, 2.0)],
        kept=[(1.0, 1.5, "今日は"), (1.5, 2.0, "晴れ"), (2.0, 2.0, "")],
        segments=[segment],
    )
    extractor = _Extractor()

    found = _assess(tmp_path, extractor)

    assert found is not None
    assert (found.engine, found.device, found.duration) == ("ctranslate2", "cuda", 2.0)
    assert found.text == "今日は 晴れ"  # a zero-length or empty segment is not written, as in an SRT
    assert (found.detected_language, found.detected_language_probability) == ("en", 0.8)
    assert found.language_probability == 0.15
    assert found.speech_ratio == 0.5
    assert found.speech_level_db == pytest.approx(TONE_DB, abs=0.05)
    assert found.background_level_db == clip_assessment.LEVEL_FLOOR_DB
    assert found.speech == [(1.0, 2.0)] and found.segments == [segment]
    [call] = calls
    assert call["language"] == "ja" and call["duration_s"] == 2.0 and call["sample_rate"] == SR
    assert not any(path.exists() for path in extractor.wavs)  # the temp WAV is removed


def test_without_the_vad_the_speech_numbers_are_null(monkeypatch, tmp_path) -> None:
    _fake_decode(monkeypatch, speech=None, kept=[])
    found = _assess(tmp_path, _Extractor())
    assert found is not None
    assert (found.speech_ratio, found.speech_level_db, found.background_level_db, found.speech) == (
        None,
        None,
        None,
        None,
    )
    assert found.text == ""


def test_a_side_with_no_samples_has_no_level(monkeypatch, tmp_path) -> None:
    _fake_decode(monkeypatch, speech=[(0.0, 2.5)], kept=[])
    found = _assess(tmp_path, _Extractor())
    assert found is not None
    assert found.speech_ratio == 1.0 and found.background_level_db is None
    assert found.speech_level_db is not None and math.isfinite(found.speech_level_db)


def test_the_told_language_may_be_missing_from_the_detection(monkeypatch, tmp_path) -> None:
    _fake_decode(monkeypatch, speech=[], kept=[], probabilities={"en": 0.8})
    found = _assess(tmp_path, _Extractor(), language="yue")
    assert found is not None
    assert found.language_probability is None
    assert found.speech_ratio == 0.0 and found.speech_level_db is None


def test_audio_that_cannot_be_extracted_is_none(monkeypatch, tmp_path) -> None:
    calls = _fake_decode(monkeypatch, speech=None, kept=[])
    extractor = _Extractor(ok=False)
    assert _assess(tmp_path, extractor) is None
    assert calls == [] and not any(path.exists() for path in extractor.wavs)

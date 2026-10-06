"""A YouTube download's subtitle step, shared by process_youtube_url and --api fetch."""

from __future__ import annotations

import threading

import pytest

from anki_miner.exceptions.youtube import TranscriptionProducedNothingError
from anki_miner.models.youtube import FetchedMedia
from anki_miner.services import youtube_postfetch
from anki_miner.services.asr import subtitle_generation
from anki_miner.services.asr.subtitle_generation import SubtitleGenResult, SubtitleGenStatus


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "abc.mp4"
    path.write_bytes(b"video")
    return path


def test_transcription_fills_the_subtitle_and_names_its_steps(test_config, video, tmp_path, monkeypatch) -> None:
    def fake(
        config,
        extractor,
        video_path,
        out_srt,
        *,
        on_extract_start,
        on_transcribe_start,
        transcribe_progress_cb,
        cancel_event,
        language,
    ):
        on_extract_start()
        on_transcribe_start()
        transcribe_progress_cb(0.5)
        out_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        return SubtitleGenResult(status=SubtitleGenStatus.SUCCESS, out_srt=out_srt)

    monkeypatch.setattr(subtitle_generation, "generate_subtitle_one", fake)
    steps: list = []
    fetched = youtube_postfetch.transcribe_fetched(
        test_config,
        object(),
        FetchedMedia(video, None, "generated"),
        tmp_path,
        threading.Event(),
        lambda step, fraction: steps.append((step, fraction)),
    )
    assert fetched.subtitle_file == tmp_path / "abc.srt" and fetched.sub_source == "generated"
    assert steps == [("extracting", None), ("transcribing", 0.0), ("transcribing", 0.5)]


def test_no_speech_is_an_error(test_config, video, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        subtitle_generation,
        "generate_subtitle_one",
        lambda *a, **k: SubtitleGenResult(status=SubtitleGenStatus.NO_SPEECH),
    )
    with pytest.raises(TranscriptionProducedNothingError):
        youtube_postfetch.transcribe_fetched(
            test_config, object(), FetchedMedia(video, None, "generated"), tmp_path, threading.Event()
        )


@pytest.mark.parametrize(
    ("ok", "cancelled", "expect"), [(True, False, "retimed"), (False, False, "kept"), (False, True, None)]
)
def test_alignment(test_config, video, tmp_path, monkeypatch, ok, cancelled, expect) -> None:
    from anki_miner.services import subtitle_retimer
    from anki_miner.services.subtitle_retimer import RetimeOutcome

    captions = tmp_path / "abc.ja.srt"
    captions.write_text("x", encoding="utf-8")

    def fake(config, video_path, in_sub, out_sub, **kwargs):
        if ok:
            out_sub.write_text("y", encoding="utf-8")
        return RetimeOutcome(ok=ok, cancelled=cancelled)

    monkeypatch.setattr(subtitle_retimer, "retime_subtitle", fake)
    steps: list = []
    result = youtube_postfetch.align_fetched(
        test_config,
        FetchedMedia(video, captions, "auto"),
        tmp_path,
        threading.Event(),
        lambda step, fraction: steps.append(step),
    )
    if expect is None:
        assert result is None
    else:
        assert result is not None
        assert result.subtitle_file == (tmp_path / "abc.ja.retimed.srt" if expect == "retimed" else captions)
    assert steps == ["aligning"]

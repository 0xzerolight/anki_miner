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


@pytest.mark.parametrize("sub_mode", ["manual_only", "auto_dub"])
@pytest.mark.parametrize(
    ("ok", "cancelled", "expect"), [(True, False, "retimed"), (False, False, "kept"), (False, True, None)]
)
def test_alignment(test_config, video, tmp_path, monkeypatch, ok, cancelled, expect, sub_mode) -> None:
    from anki_miner.services import subtitle_retimer
    from anki_miner.services.subtitle_retimer import RetimeOutcome

    captions = tmp_path / "abc.ja.srt"
    captions.write_text("x", encoding="utf-8")

    def fake(config, video_path, in_sub, out_sub, **kwargs):
        # The captions were timed against this very stream: no framerate ratio can be right.
        assert kwargs["fix_framerate"] is False
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
        sub_mode=sub_mode,
    )
    if expect is None:
        assert result is None
    else:
        assert result is not None
        assert result.subtitle_file == (tmp_path / "abc.ja.retimed.srt" if expect == "retimed" else captions)
    assert steps == ["aligning"]


def test_native_auto_captions_are_not_aligned(test_config, video, tmp_path, monkeypatch) -> None:
    """YouTube timed them from this very audio; the aligners only find false optima on them (#137)."""
    from anki_miner.services import subtitle_retimer

    captions = tmp_path / "abc.ja.srt"
    captions.write_text("x", encoding="utf-8")
    called: list[int] = []
    monkeypatch.setattr(subtitle_retimer, "retime_subtitle", lambda *a, **k: called.append(1))
    fetched = FetchedMedia(video, captions, "auto")
    steps: list = []

    result = youtube_postfetch.align_fetched(
        test_config,
        fetched,
        tmp_path,
        threading.Event(),
        lambda step, fraction: steps.append(step),
        sub_mode="auto_only",
    )

    assert result == fetched
    assert called == []
    assert steps == []

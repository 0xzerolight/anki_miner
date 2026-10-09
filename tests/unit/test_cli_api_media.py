"""--api media: clips and pictures for named lines, no parse, no dictionary, no Anki."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.cli.api import files, media
from anki_miner.models import MediaData
from anki_miner.services.media_extractor import MediaExtractorService

SRT = (
    "1\n00:00:12,480 --> 00:00:14,900\n約束したでしょう\n\n"
    "2\n00:00:15,020 --> 00:00:17,600\n今日こそは言うよ\n\n"
    "3\n00:00:17,640 --> 00:00:20,100\nちゃんと\n"
)


@pytest.fixture
def episode(tmp_path):
    (tmp_path / "v.mkv").write_bytes(b"video")
    (tmp_path / "s.srt").write_text(SRT, encoding="utf-8")
    return tmp_path


def _file(tmp_path: Path, lines: list[dict], **top) -> files.MediaFile:
    return files.parse_media_file(
        {
            "schema": 1,
            "run_dir": str(tmp_path),
            "language": "ja",
            **top,
            "episodes": [
                {
                    "run_id": "ep-01",
                    "video_file": str(tmp_path / "v.mkv"),
                    "subtitle_file": str(tmp_path / "s.srt"),
                    "lines": lines,
                }
            ],
        }
    )


def _cut(video_file, word, temp_folder=None, **_kw) -> MediaData:
    # A name per line, as the real extractor gives one per cut: the lines are cut side by side.
    picture, audio = temp_folder / f"cut-{word.surface}.jpg", temp_folder / f"cut-{word.surface}.mp3"
    picture.write_bytes(b"p")
    audio.write_bytes(b"a")
    return MediaData(
        screenshot_path=picture, audio_path=audio, screenshot_filename=picture.name, audio_filename=audio.name
    )


def test_media_cuts_each_named_line_and_lists_it(episode, test_config) -> None:
    config = replace(test_config, merge_incomplete_cues=False)  # a line left without line_expansion stays one line
    with (
        patch.object(media.settings, "resolve_run_config", return_value=config),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_video"),
        patch.object(MediaExtractorService, "extract_media", side_effect=_cut) as cut,
        patch.object(media, "MediaExtractorService", wraps=MediaExtractorService) as extractor,
    ):
        [verdict] = media.media_runs(
            _file(episode, [{"line_start": 15.0}, {"line_start": 12.48, "line_expansion": [0, 1]}], still_height=480)
        )
    # still_height reaches the still and, through the cut config, an animated picture.
    [(cut_config,), kwargs] = extractor.call_args
    assert kwargs == {"still_height": 480} and cut_config.screenshot_animated_height == 480
    assert verdict["file"] == "media-1.json"
    first, second = json.loads((episode / "ep-01" / "media-1.json").read_text(encoding="utf-8"))["lines"]
    assert first == {
        "line_start": 15.02,
        "start": 15.02,
        "end": 17.6,
        "text": "今日こそは言うよ",
        "picture": "media-1/1.jpg",
        "audio": "media-1/1.mp3",
    }
    assert (second["text"], second["end"]) == ("約束したでしょう 今日こそは言うよ", 17.6)
    assert (episode / "ep-01" / "media-1" / "2.jpg").read_bytes() == b"p"
    assert cut.call_count == 2


def test_media_cuts_its_lines_side_by_side(episode, test_config) -> None:
    together = threading.Barrier(2, timeout=5)

    def cut(video_file, word, temp_folder=None, **kw) -> MediaData:
        together.wait()  # one line at a time never gets past this
        return _cut(video_file, word, temp_folder, **kw)

    with (
        patch.object(media.settings, "resolve_run_config", return_value=replace(test_config, max_parallel_workers=2)),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_video"),
        patch.object(MediaExtractorService, "extract_media", side_effect=cut),
    ):
        [verdict] = media.media_runs(_file(episode, [{"line_start": 12.48}, {"line_start": 15.0}]))
    assert verdict["ok"] is True
    lines = json.loads((episode / "ep-01" / "media-1.json").read_text(encoding="utf-8"))["lines"]
    assert [(line["line_start"], line["picture"]) for line in lines] == [
        (12.48, "media-1/1.jpg"),
        (15.02, "media-1/2.jpg"),
    ]


def test_a_line_whose_picture_failed_keeps_its_audio(episode, test_config) -> None:
    # extract_media_batch keeps only the lines whose picture was cut: media keeps whichever cut worked.
    def audio_only(video_file, word, temp_folder=None, **_kw) -> MediaData:
        audio = temp_folder / f"cut-{word.surface}.mp3"
        audio.write_bytes(b"a")
        return MediaData(audio_path=audio, audio_filename=audio.name)

    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_video"),
        patch.object(MediaExtractorService, "extract_media", side_effect=audio_only),
    ):
        media.media_runs(_file(episode, [{"line_start": 15.0}]))
    [line] = json.loads((episode / "ep-01" / "media-1.json").read_text(encoding="utf-8"))["lines"]
    assert (line["picture"], line["audio"]) == (None, "media-1/1.mp3")


def test_media_never_loads_a_tokenizer_or_a_dictionary(episode, test_config) -> None:
    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_video"),
        patch.object(MediaExtractorService, "extract_media", side_effect=_cut),
        patch("anki_miner.services.subtitle_parser.get_shared_tagger", side_effect=AssertionError("tagger")),
        patch("anki_miner.gui.utils.service_factory.create_shared_lookup_services", side_effect=AssertionError("dict")),
    ):
        [verdict] = media.media_runs(_file(episode, [{"line_start": 1.0}]))
    assert verdict["ok"] is True


def test_a_subtitle_with_no_lines_is_unreadable(episode, test_config) -> None:
    (episode / "s.srt").write_text("", encoding="utf-8")
    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_video"),
    ):
        [verdict] = media.media_runs(_file(episode, [{"line_start": 1.0}]))
    assert verdict["error"] == "SUBTITLE_UNREADABLE"

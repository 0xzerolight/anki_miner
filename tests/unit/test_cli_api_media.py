"""--api media: clips and pictures for named lines, no parse, no dictionary, no Anki."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.cli.api import files, media
from anki_miner.models import MediaData

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
    picture, audio = temp_folder / "cut.jpg", temp_folder / "cut.mp3"
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
        patch.object(media.MediaExtractorService, "extract_media", side_effect=_cut) as cut,
    ):
        [verdict] = media.media_runs(
            _file(episode, [{"line_start": 15.0}, {"line_start": 12.48, "line_expansion": [0, 1]}], still_height=480)
        )
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


def test_media_never_loads_a_tokenizer_or_a_dictionary(episode, test_config) -> None:
    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_video"),
        patch.object(media.MediaExtractorService, "extract_media", side_effect=_cut),
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

"""--api media: clips and pictures for named lines, no parse, no dictionary, no Anki."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from anki_miner.cli.api import files, media
from anki_miner.cli.api.contract import ApiError
from anki_miner.models import MediaData
from anki_miner.models.reading import ImageRef, ReadingDocument, ReadingUnit
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.reading.images import manga_crop_box

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
        "unit_index": None,
        "location": None,
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


# --- reading and audio episodes ------------------------------------------------------------------


def _page(tmp_path: Path) -> Path:
    page = tmp_path / "p012.png"
    Image.new("RGB", (400, 600), "white").save(page)
    return page


def _reading_file(tmp_path: Path, source: Path, lines: list[dict]) -> files.MediaFile:
    episode = {"run_id": "ep-01", "reading_file": str(source), "lines": lines}
    return files.parse_media_file({"schema": 1, "run_dir": str(tmp_path), "language": "ja", "episodes": [episode]})


def _volume(tmp_path: Path) -> tuple[Path, ReadingDocument]:
    source = tmp_path / "漫画 第1巻.mokuro"
    source.write_text("{}", encoding="utf-8")
    page = ImageRef(_page(tmp_path))
    units = [
        ReadingUnit(
            text=text,
            index=i,
            location_label="p.12",
            image_ref=page,
            block_box=(100, 100 + 100 * i, 200, 150 + 100 * i),
        )
        for i, text in enumerate(["えっ", "宿題やった？", "まだ"])
    ]
    return source, ReadingDocument(title="漫画", kind="manga", series="漫画", episode="第1巻", units=units)


def test_media_for_a_manga_volume_gives_each_units_crop_and_no_clip(tmp_path, test_config) -> None:
    source, document = _volume(tmp_path)
    lines = [{"unit_index": 1, "line_expansion": [1, 1]}, {"unit_index": 7}]
    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "require_ffmpeg", side_effect=AssertionError("ffmpeg")),
        patch.object(media, "open_reading", return_value=document) as opened,
        patch("anki_miner.services.subtitle_parser.get_shared_tagger", side_effect=AssertionError("tagger")),
    ):
        [verdict] = media.media_runs(_reading_file(tmp_path, source, lines))
    assert verdict["file"] == "media-1.json" and opened.call_args.args[2] == source.resolve()
    first, past_the_end = json.loads((tmp_path / "ep-01" / "media-1.json").read_text(encoding="utf-8"))["lines"]
    assert first == {
        "line_start": None,
        "start": None,
        "end": None,
        "unit_index": 1,
        "location": "p.12",
        "text": "えっ 宿題やった？ まだ",
        "picture": "media-1/1.jpg",
        "audio": None,
    }
    left, top, right, bottom = manga_crop_box((100, 200, 200, 250), (400, 600))  # unit 1's block, padded
    with Image.open(tmp_path / "ep-01" / "media-1" / "1.jpg") as crop:
        assert crop.size == (right - left, bottom - top)
    assert past_the_end == dict.fromkeys(first) | {"unit_index": 7}
    assert sorted(p.name for p in (tmp_path / "ep-01" / "media-1").iterdir()) == ["1.jpg"]


def test_media_for_a_book_gives_its_cover_and_a_subtitle_file_no_picture(tmp_path, test_config) -> None:
    cover = ImageRef(_page(tmp_path))
    book = ReadingDocument(
        title="吾輩は猫である",
        kind="book",
        series="Books",
        episode="吾輩は猫である",
        units=[ReadingUnit(text="名前はまだ無い。", index=0, location_label="一", image_ref=cover)],
    )
    subtitle = replace(
        book, kind="subtitle", units=[ReadingUnit(text="名前はまだ無い。", index=0, location_label="0:01")]
    )
    (tmp_path / "book.epub").write_bytes(b"epub")
    pictures = []
    for document in (book, subtitle):
        with (
            patch.object(media.settings, "resolve_run_config", return_value=test_config),
            patch.object(media, "open_reading", return_value=document),
        ):
            media.media_runs(_reading_file(tmp_path, tmp_path / "book.epub", [{"unit_index": 0}]))
        [line] = json.loads(sorted((tmp_path / "ep-01").glob("media-*.json"))[-1].read_text(encoding="utf-8"))["lines"]
        pictures.append(line["picture"])
    assert pictures == ["media-1/1.jpg", None]
    with Image.open(tmp_path / "ep-01" / "media-1" / "1.jpg") as picture:
        assert picture.size == (400, 600)  # the whole cover


def test_media_for_a_volume_that_does_not_load_is_source_unreadable(tmp_path, test_config) -> None:
    source, _document = _volume(tmp_path)
    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "open_reading", side_effect=ApiError("SOURCE_UNREADABLE", "no .mokuro")),
    ):
        [verdict] = media.media_runs(_reading_file(tmp_path, source, [{"unit_index": 0}]))
    assert (verdict["error"], verdict["file"]) == ("SOURCE_UNREADABLE", None)


def _audio(tmp_path: Path, lines: list[dict]) -> files.MediaFile:
    (tmp_path / "book.m4b").write_bytes(b"audio")
    episode = {"run_id": "ep-01", "audio_file": str(tmp_path / "book.m4b"), "subtitle_file": str(tmp_path / "s.srt")}
    return files.parse_media_file(
        {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "episodes": [{**episode, "lines": lines}]}
    )


@pytest.mark.parametrize("has_cover", [True, False])
def test_media_for_an_audio_file_gives_the_clip_and_its_cover(episode, test_config, has_cover) -> None:
    def cover_art(media_file, temp_folder, **_kw):
        if not has_cover:
            return None
        made = temp_folder / "audiobook_cover_abc.jpg"
        made.write_bytes(b"cover")
        return made

    def audio_only(video_file, word, temp_folder=None, *, audio_only=False, **_kw) -> MediaData:
        assert audio_only is True and video_file == episode.resolve() / "book.m4b"
        clip = temp_folder / f"cut-{word.surface}.mp3"
        clip.write_bytes(b"a")
        return MediaData(audio_path=clip, audio_filename=clip.name)

    with (
        patch.object(media.settings, "resolve_run_config", return_value=test_config),
        patch.object(media, "require_ffmpeg"),
        patch.object(media, "check_audio") as check_audio,
        patch.object(media, "check_video", side_effect=AssertionError("video")),
        patch.object(MediaExtractorService, "extract_media", side_effect=audio_only),
        patch.object(MediaExtractorService, "extract_cover_art", side_effect=cover_art),
    ):
        [verdict] = media.media_runs(_audio(episode, [{"line_start": 12.48}, {"line_start": 15.0}]))
    assert verdict["ok"] is True
    check_audio.assert_called_once()
    lines = json.loads((episode / "ep-01" / "media-1.json").read_text(encoding="utf-8"))["lines"]
    if has_cover:
        assert [(line["picture"], line["audio"]) for line in lines] == [
            ("media-1/1.jpg", "media-1/1.mp3"),
            ("media-1/2.jpg", "media-1/2.mp3"),
        ]
        assert (episode / "ep-01" / "media-1" / "2.jpg").read_bytes() == b"cover"
    else:
        assert [line["picture"] for line in lines] == [None, None]
    assert not (episode / "ep-01" / "media-1" / "audiobook_cover_abc.jpg").exists()
    assert (lines[0]["unit_index"], lines[0]["location"], lines[0]["line_start"]) == (None, None, 12.48)

"""The API's sources besides a video: reading sources, OCR still owed, audio files, subtitle tracks; and readiness."""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.cli.api import readiness, sources
from anki_miner.cli.api.contract import ApiError
from anki_miner.cli.api.jobs import RunFailed
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.services.reading.web_article import ArticleFetchError
from anki_miner.services.track_extractor import MediaTracks
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream


def _mokuro(path: Path, volume: str) -> Path:
    data = {"version": "0.1.0", "title": "MyManga", "title_uuid": "t", "volume": volume, "volume_uuid": "v"}
    path.write_text(json.dumps({**data, "pages": []}), encoding="utf-8")
    return path


def _refused(call, *args, **kwargs) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_a_file_that_is_no_reading_source_is_a_bad_run_file(tmp_path) -> None:
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    assert _refused(sources.reading_ref, video).code == "BAD_RUN_FILE"
    assert sources.reading_kind(tmp_path / "book.EPUB") == "book"
    assert sources.reading_kind(tmp_path / "ep01.ja.srt") == "subtitle"
    assert sources.reading_kind(tmp_path / "vol.cbz") == "manga"


def test_a_volume_without_its_mokuro_says_ocr_is_owed(tmp_path) -> None:
    archive = tmp_path / "Vol1.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("001.jpg", b"jpeg")
    pages = tmp_path / "Vol2"
    pages.mkdir()
    (pages / "001.png").write_bytes(b"png")
    for volume in (archive, pages):
        error = _refused(sources.reading_ref, volume)
        assert error.code == "SOURCE_UNREADABLE" and "Run ocr" in error.message
        assert sources.needs_ocr(volume)
    _mokuro(tmp_path / "Vol1.mokuro", "Vol1")
    assert not sources.needs_ocr(archive)
    assert sources.reading_ref(archive).kind == "mokuro"


def test_a_folder_of_several_volumes_must_name_one(tmp_path) -> None:
    title = tmp_path / "MyManga"
    title.mkdir()
    _mokuro(title / "Vol1.mokuro", "Vol1")
    _mokuro(title / "Vol2.mokuro", "Vol2")
    error = _refused(sources.reading_ref, title)
    assert error.code == "SOURCE_UNREADABLE" and "2 manga volumes" in error.message


def test_open_reading_loads_a_book_and_refuses_a_missing_one(tmp_path, test_config) -> None:
    book = tmp_path / "book.txt"
    book.write_text("吾輩は猫である。名前はまだ無い。\n", encoding="utf-8")
    parser = create_line_parser(test_config)
    document = sources.open_reading(test_config, parser, book, cancel_check=lambda: False)
    assert [unit.index for unit in document.units] == list(range(len(document.units))) and document.units
    missing = _refused(sources.open_reading, test_config, parser, tmp_path / "gone.epub", cancel_check=lambda: False)
    assert missing.code == "SOURCE_UNREADABLE"
    cancelled = _refused(sources.open_reading, test_config, parser, book, cancel_check=lambda: True)
    assert cancelled.code == "CANCELLED"


def test_a_web_article_that_fails_transiently_says_so(monkeypatch, tmp_path, test_config) -> None:
    def fail(url, *, rules=None):
        raise ArticleFetchError(url, "network", "The page could not be reached.", transient=True)

    monkeypatch.setattr(sources, "fetch_article", fail)
    with pytest.raises(RunFailed) as caught:
        sources.open_reading(
            test_config, create_line_parser(test_config), "https://example.com/a", cancel_check=lambda: False
        )
    assert caught.value.code == "SOURCE_UNREADABLE" and caught.value.transient


def _tracks(monkeypatch, tracks: MediaTracks) -> None:
    monkeypatch.setattr(sources, "probe_tracks", lambda path, ffprobe: tracks)


def test_resolve_track_by_its_stream_index(monkeypatch, tmp_path, test_config) -> None:
    ass = SubtitleStream(index=2, sub_index=0, codec_name="ass", language_tag="jpn", title=None, is_text=True)
    pgs = SubtitleStream(
        index=3, sub_index=1, codec_name="hdmv_pgs_subtitle", language_tag="jpn", title=None, is_text=False
    )
    audio = AudioStream(1, 0, "jpn", None, "aac", 2, True)
    _tracks(monkeypatch, MediaTracks(subtitles=(ass, pgs), audio=(audio,)))
    video = tmp_path / "ep.mkv"
    assert sources.resolve_track(test_config, video, 3) == pgs
    assert "audio track" in _refused(sources.resolve_track, test_config, video, 1).message
    assert _refused(sources.resolve_track, test_config, video, 9).code == "TRACK_NOT_FOUND"


def test_check_audio(monkeypatch, tmp_path, test_config) -> None:
    monkeypatch.setattr(sources, "get_media_duration_seconds", lambda path, ffprobe: None)
    _tracks(monkeypatch, MediaTracks())
    assert _refused(sources.check_audio, test_config, tmp_path / "a.m4b").code == "AUDIO_UNREADABLE"
    monkeypatch.setattr(sources, "get_media_duration_seconds", lambda path, ffprobe: 12.0)
    sources.check_audio(test_config, tmp_path / "a.m4b")


@pytest.mark.parametrize(
    ("route", "model", "engine", "expected"),
    [
        (True, False, False, None),  # the whisper.cpp route needs no CT2 engine
        (False, False, True, "speech model large-v3"),
        (False, True, False, "speech engine"),
        (False, True, True, None),
    ],
)
def test_speech_problem(monkeypatch, test_config, route, model, engine, expected) -> None:
    from anki_miner.services.asr import _engine, model_availability, model_manager

    monkeypatch.setattr(model_availability, "whisper_cpp_route_ready", lambda config: route)
    monkeypatch.setattr(model_manager, "is_downloaded", lambda name, root: model)
    monkeypatch.setattr(_engine, "available", lambda: engine)
    problem = readiness.speech_problem(replace(test_config, asr_model="large-v3"))
    assert problem is None if expected is None else expected in (problem or "")

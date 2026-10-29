"""--api inspect: books, text, owed manga OCR, audio and video tracks, unit lists, per-source errors."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.cli.api import inspect, sources
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.services.track_extractor import MediaTracks
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream

_CONTAINER = (
    '<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>\n"
)
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _xhtml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>x</title></head><body>{body}</body></html>\n"
    )


def _epub(folder: Path) -> Path:
    """Chapters 一 and 二 in the table of contents, and between them a file it does not name (ch.N)."""
    manifest = (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="cov" href="cover.png" media-type="image/png" properties="cover-image"/>'
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c3" href="c3.xhtml" media-type="application/xhtml+xml"/>'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>吾輩は猫である</dc:title></metadata>'
        f'<manifest>{manifest}</manifest><spine><itemref idref="c1"/><itemref idref="c2"/><itemref idref="c3"/>'
        "</spine></package>\n"
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>toc</title></head><body><nav epub:type="toc"><ol>'
        '<li><a href="c1.xhtml">一</a></li><li><a href="c3.xhtml">二</a></li></ol></nav></body></html>\n'
    )
    path = folder / "吾輩は猫である.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/cover.png", _PNG)
        zf.writestr("OEBPS/c1.xhtml", _xhtml("<p>吾輩は猫である。名前はまだ無い。</p>"))
        zf.writestr("OEBPS/c2.xhtml", _xhtml("<p>挿話だ。</p>"))
        zf.writestr("OEBPS/c3.xhtml", _xhtml("<p>どこで生れたか。</p>"))
    return path


@pytest.fixture
def verdict(capfd, monkeypatch):
    from anki_miner.cli import api, entry

    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)

    def run(*argv: str) -> dict:
        assert entry.main(["--api", *argv]) == 0
        [line] = capfd.readouterr().out.splitlines()
        return json.loads(line)

    return run


@pytest.fixture
def inspect_file(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()

    def write(*sources_: dict, **header: object) -> str:
        path = tmp_path / "inspect.json"
        data = {"schema": 1, "language": "ja", "run_dir": str(runs), "sources": list(sources_), **header}
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    return write


def _result(v: dict) -> list[dict]:
    assert v["ok"] and v["command"] == "inspect" and v["error"] is None, v
    return v["result"]["sources"]


def test_a_book_with_its_cover_chapters_and_unit_list(verdict, inspect_file, tmp_path) -> None:
    book = _epub(tmp_path)
    [entry] = _result(verdict("inspect", inspect_file({"path": str(book), "units": True, "run_id": "job-1"})))
    assert entry == {
        "path": str(book),
        "kind": "book",
        "title": "吾輩は猫である",
        "series": "Books",
        "episode": "吾輩は猫である",
        "cover": True,
        "units": 4,
        "chapters": [{"title": "一", "index": 0}, {"title": "二", "index": 3}],  # the unnamed file is no chapter
        "needs": [],
        "warnings": [],
        "duration": None,
        "tracks": [],
        "file": "inspect-1.json",
        "error": None,
        "message": None,
    }
    written = json.loads((tmp_path / "runs" / "job-1" / "inspect-1.json").read_text(encoding="utf-8"))
    assert written["schema"] == 1 and written["run_id"] == "job-1"
    assert written["units"][:2] == [
        {"index": 0, "location": "一", "text": "吾輩は猫である。"},
        {"index": 1, "location": "一", "text": "名前はまだ無い。"},
    ]
    # The units mine reads: open_reading with the language's line parser, index for index.
    document = sources.open_reading(
        inspect.settings.resolve_run_config(None, "ja", {}),
        create_line_parser(inspect.settings.resolve_run_config(None, "ja", {})),
        book,
        cancel_check=lambda: False,
    )
    assert written["units"] == [
        {"index": u.index, "location": u.location_label, "text": u.text} for u in document.units
    ]
    # A second list for the same run_id is the next file.
    [again] = _result(verdict("inspect", inspect_file({"path": str(book), "units": True, "run_id": "job-1"})))
    assert again["file"] == "inspect-2.json"


def test_a_text_with_headings_and_one_without(verdict, inspect_file, tmp_path) -> None:
    novel = tmp_path / "猫.txt"
    novel.write_text(
        "吾輩は猫である\n夏目漱石\n\n一［＃「一」は大見出し］\n　吾輩は猫である。名前はまだ無い。\n\n"
        "二［＃「二」は大見出し］\n　どこで生れたか。\n",
        encoding="utf-8",
    )
    plain = tmp_path / "plain.txt"
    plain.write_text("前書き。\n\n本文だ。次の文。\n", encoding="utf-8")
    headed, unheaded = _result(verdict("inspect", inspect_file({"path": str(novel)}, {"path": str(plain)})))
    assert headed["chapters"] == [{"title": "一", "index": 0}, {"title": "二", "index": 3}]
    assert (headed["units"], headed["cover"], headed["file"]) == (5, False, None)
    assert unheaded["chapters"] == [] and unheaded["units"] == 3  # ¶N labels name no chapter


def test_a_volume_without_its_mokuro_needs_ocr(verdict, inspect_file, tmp_path) -> None:
    volume = tmp_path / "漫画" / "漫画 第1巻"
    volume.mkdir(parents=True)
    (volume / "001.jpg").write_bytes(b"jpeg")
    [entry] = _result(verdict("inspect", inspect_file({"path": str(volume), "units": True, "run_id": "job-2"})))
    assert {k: entry[k] for k in ("kind", "title", "series", "episode", "cover", "units", "needs", "file")} == {
        "kind": "manga",
        "title": "漫画",
        "series": "漫画",
        "episode": "漫画 第1巻",
        "cover": True,
        "units": 0,
        "needs": ["ocr"],
        "file": None,
    }
    assert entry["error"] is None and not (tmp_path / "runs" / "job-2").exists()


def _probe(monkeypatch, *, video: bool, duration: float | None, tracks: MediaTracks) -> None:
    monkeypatch.setattr(inspect, "binary_available", lambda resolved: True)
    monkeypatch.setattr(inspect, "get_primary_video_codec", lambda path, ffprobe: "h264" if video else None)
    monkeypatch.setattr(inspect, "get_media_duration_seconds", lambda path, ffprobe: duration)
    monkeypatch.setattr(inspect, "probe_tracks", lambda path, ffprobe: tracks)


def test_a_video_lists_its_tracks_by_stream_index(verdict, inspect_file, tmp_path, monkeypatch) -> None:
    tracks = MediaTracks(
        subtitles=(
            SubtitleStream(
                index=3, sub_index=1, codec_name="hdmv_pgs_subtitle", language_tag="jpn", title="BD", is_text=False
            ),
            SubtitleStream(
                index=2, sub_index=0, codec_name="ass", language_tag="jpn", title="日本語", is_text=True, is_forced=True
            ),
        ),
        audio=(AudioStream(1, 0, "jpn", "Stereo", "aac", 2, True),),
    )
    _probe(monkeypatch, video=True, duration=1440.1234, tracks=tracks)
    video = tmp_path / "ダンジョン飯" / "ダンジョン飯 01.mkv"
    [entry] = _result(verdict("inspect", inspect_file({"path": str(video)})))
    assert (entry["kind"], entry["title"], entry["series"], entry["episode"]) == (
        "video",
        "ダンジョン飯 01",
        "ダンジョン飯",
        "ダンジョン飯 01",
    )
    assert (entry["duration"], entry["units"], entry["cover"], entry["chapters"]) == (1440.123, None, False, [])
    assert entry["tracks"] == [
        {
            "index": 1,
            "position": 0,
            "kind": "audio",
            "codec": "aac",
            "language": "jpn",
            "title": "Stereo",
            "default": True,
            "forced": None,
            "text": None,
        },
        {
            "index": 2,
            "position": 0,
            "kind": "subtitle",
            "codec": "ass",
            "language": "jpn",
            "title": "日本語",
            "default": False,
            "forced": True,
            "text": True,
        },
        {
            "index": 3,
            "position": 1,
            "kind": "subtitle",
            "codec": "hdmv_pgs_subtitle",
            "language": "jpn",
            "title": "BD",
            "default": False,
            "forced": False,
            "text": False,
        },
    ]


def test_an_audio_file_by_its_extension(verdict, inspect_file, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sources, "check_audio", lambda config, path: None)
    tracks = MediaTracks(audio=(AudioStream(0, 0, None, None, "aac", 2, True),))
    _probe(monkeypatch, video=True, duration=3600.0, tracks=tracks)  # the video stream is its cover picture
    audio = tmp_path / "吾輩は猫である.m4b"
    [entry] = _result(verdict("inspect", inspect_file({"path": str(audio), "units": True, "run_id": "job-3"})))
    assert (entry["kind"], entry["title"], entry["series"], entry["episode"]) == (
        "audio",
        "吾輩は猫である",
        "Audio",
        "吾輩は猫である",
    )
    assert (entry["cover"], entry["duration"], entry["file"]) == (True, 3600.0, None)
    assert [t["kind"] for t in entry["tracks"]] == ["audio"]


def test_without_ffprobe_audio_and_video_are_setup_errors_and_books_still_read(
    verdict, inspect_file, tmp_path, monkeypatch
) -> None:
    """A missing ffprobe is no reason to call a good file unreadable."""
    monkeypatch.setattr(inspect, "binary_available", lambda resolved: False)
    paths = [tmp_path / "ep01.mkv", tmp_path / "book.m4b", _epub(tmp_path)]
    entries = _result(verdict("inspect", inspect_file(*({"path": str(p)} for p in paths))))
    assert [e["error"] for e in entries] == ["SETUP_ERROR", "SETUP_ERROR", None]
    assert "ffprobe" in entries[0]["message"] and entries[2]["kind"] == "book"


def test_each_failing_source_carries_its_code(verdict, inspect_file, tmp_path, monkeypatch) -> None:
    _probe(monkeypatch, video=False, duration=None, tracks=MediaTracks())
    (tmp_path / "notes.xyz").write_text("x", encoding="utf-8")
    book = _epub(tmp_path)
    paths = [tmp_path / "missing.epub", tmp_path / "notes.xyz", tmp_path / "gone.mkv", tmp_path / "gone.mp3", book]
    entries = _result(verdict("inspect", inspect_file(*({"path": str(p)} for p in paths))))
    assert [e["error"] for e in entries] == [
        "SOURCE_UNREADABLE",
        "BAD_RUN_FILE",
        "VIDEO_UNREADABLE",
        "AUDIO_UNREADABLE",
        None,
    ]
    failed = entries[1]
    assert failed["path"] == str(tmp_path / "notes.xyz") and "not a source inspect reads" in failed["message"]
    assert all(failed[k] is None for k in inspect._KEYS if k not in ("path", "error", "message"))
    assert entries[-1]["kind"] == "book"


@pytest.mark.parametrize(
    ("source", "header", "needle"),
    [
        ({"path": "x.epub", "units": True}, {}, "run_id is required"),
        ({"path": "x.epub", "units": True, "run_id": "a"}, {"run_dir": None}, "run_dir is required"),
        ({"path": "x.epub", "units": "yes"}, {}, "true or false"),
        ({"path": "x.epub", "lines": True}, {}, "unknown keys"),
        ({"path": "x.epub", "run_id": "bad id"}, {}, "letters, digits"),
    ],
)
def test_a_bad_inspect_file_is_refused(verdict, inspect_file, source, header, needle) -> None:
    v = verdict("inspect", inspect_file(source, **header))
    assert v["error"] == "BAD_RUN_FILE" and needle in v["message"] and v["runs"] == []


def test_inspect_needs_no_run_dir_without_unit_lists(verdict, tmp_path) -> None:
    path = tmp_path / "inspect.json"
    path.write_text(
        json.dumps({"schema": 1, "language": "ja", "sources": [{"path": str(_epub(tmp_path))}]}), encoding="utf-8"
    )
    [entry] = _result(verdict("inspect", str(path)))
    assert entry["units"] == 4


def test_an_unknown_language_refuses_the_call(verdict, inspect_file, tmp_path) -> None:
    assert verdict("inspect", inspect_file({"path": str(tmp_path)}, language="xx"))["error"] == "BAD_RUN_FILE"


def test_a_signal_cancels_the_whole_call(inspect_file, tmp_path) -> None:
    import threading

    from anki_miner.cli.api.contract import ApiError

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ApiError) as caught:
        inspect.call(Path(inspect_file({"path": str(_epub(tmp_path))})), cancel)
    assert caught.value.code == "CANCELLED"

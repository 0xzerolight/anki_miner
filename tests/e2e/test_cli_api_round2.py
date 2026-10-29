"""Round 2 of the --api against the real pipeline: books, manga, audio episodes, inspect and extract.

A subprocess, a seeded home, a fake Anki and real ffmpeg, as test_cli_api.py.
Marked ``network`` (loopback FakeAnkiConnect), not ``e2e``, so it runs in the gate.
The speech and OCR commands need downloaded models and are not run here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from anki_miner.gui.utils.config_manager import GUIConfigManager
from tests.e2e.app_config import build_app_config
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video
from tests.e2e.test_cli_api import _EXTRA_FIELDS, DECK, _api, _plain

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not on PATH"
    ),
]

_SRT = Path(__file__).parent / "assets" / "e2e.srt"
#: The third and fourth sentences are the same text: only unit_index names the fourth.
_BOOK = "新しい本を買いました。美味しい料理を食べる。今日は学校で勉強する。今日は学校で勉強する。\n"


@pytest.fixture
def home(isolated_home: Path, fake_anki):
    """A seeded home whose deck the run file's overlay names (test_cli_api.py's, without the wrong saved deck)."""
    pytest.importorskip("fugashi")
    seed_offline_dict(isolated_home / "dicts")
    config = build_app_config(
        E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url), isolated_home, bypass_known_words=True
    )
    GUIConfigManager.save_config(config)
    return isolated_home, fake_anki


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _mine(tmp_path: Path, runs: Path, episode: dict, *, command: str = "mine", dry_run: bool = False) -> Path:
    run = {
        "schema": 1,
        "run_dir": str(runs),
        "language": "ja",
        "config": {
            "anki_deck_name": DECK,
            "min_frequency_rank": 0,
            "max_frequency_rank": 0,
            "anki_fields": _EXTRA_FIELDS,
        },
        "episodes": [episode],
    }
    if dry_run:
        run["dry_run"] = True
    return _write(tmp_path / f"{command}.json", run)


def _result(runs: Path, run_id: str, name: str) -> dict:
    return json.loads((runs / run_id / name).read_text(encoding="utf-8"))


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def test_api_book_inspect_then_mine_by_unit(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    runs = tmp_path / "runs"
    runs.mkdir()
    book = tmp_path / "本の話.txt"
    book.write_text(_BOOK, encoding="utf-8")

    inspect_file = _write(
        tmp_path / "inspect.json",
        {
            "schema": 1,
            "language": "ja",
            "run_dir": str(runs),
            "sources": [{"path": str(book), "units": True, "run_id": "bk"}],
        },
    )
    verdict = _api(test_home, "inspect", str(inspect_file))
    [source] = verdict["result"]["sources"]
    assert verdict["ok"] is True and source["kind"] == "book" and source["error"] is None, verdict
    units = _result(runs, "bk", source["file"])["units"]
    assert source["units"] == len(units) == 4
    second = [u["index"] for u in units if u["text"].startswith("今日は")][1]

    episode = {
        "run_id": "bk",
        "reading_file": str(book),
        "words": [
            {"word": "学校", "unit_index": second},
            {"word": "料理", "line_text": "美味しい料理を食べる"},
            {"word": "本"},
        ],
    }
    dry = _api(test_home, "mine", str(_mine(tmp_path, runs, episode, dry_run=True)))
    assert dry["ok"] is True, dry
    assert [w["status"] for w in _result(runs, "bk", dry["runs"][0]["file"])["words"]] == ["ready"] * 3

    verdict = _api(test_home, "mine", str(_mine(tmp_path, runs, episode)))
    assert verdict["ok"] is True and verdict["runs"][0]["failure_is_transient"] is False, verdict
    rows = _result(runs, "bk", verdict["runs"][0]["file"])["words"]
    assert [(r["word"], r["status"]) for r in rows] == [("学校", "created"), ("料理", "created"), ("本", "created")]
    assert [r["unit_index"] for r in rows] == [second, 1, 0]
    assert all(r["location"] and r["line_start"] is None and r["start"] is None for r in rows)
    notes = {_plain(n["Front"]): n for n in fake.notes(DECK)}
    assert _plain(notes["料理"]["Sentence"]) == rows[1]["sentence"] == units[1]["text"]

    # render on the same file: the fields, and nothing more reaches Anki.
    rendered = _api(test_home, "render", str(_mine(tmp_path, runs, episode, command="render")))
    rows = _result(runs, "bk", rendered["runs"][0]["file"])["words"]
    assert [r["status"] for r in rows] == ["rendered"] * 3 and len(fake.notes(DECK)) == 3


def test_api_manga_volume_mines_its_crop_and_media_cuts_it(home, tmp_path) -> None:
    from PIL import Image

    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    runs = tmp_path / "runs"
    runs.mkdir()
    pages = tmp_path / "Vol1"
    pages.mkdir()
    Image.new("RGB", (800, 1200), "white").save(pages / "001.png")
    block = {"box": [100, 100, 300, 400], "vertical": True, "font_size": 24, "lines": ["新しい本を買いました"]}
    page = {"version": "0.2.5", "img_width": 800, "img_height": 1200, "img_path": "001.png", "blocks": [block]}
    volume = {"version": "0.2.5", "title": "漫画", "title_uuid": "t", "volume": "Vol1", "volume_uuid": "v"}
    mokuro = _write(tmp_path / "Vol1.mokuro", {**volume, "pages": [page]})

    episode = {"run_id": "mg", "reading_file": str(mokuro), "words": [{"word": "本", "unit_index": 0}]}
    verdict = _api(test_home, "mine", str(_mine(tmp_path, runs, episode)))
    [row] = _result(runs, "mg", verdict["runs"][0]["file"])["words"]
    assert row["status"] == "created" and row["location"] == "p.1" and row["media_missing"] == [], row
    [note] = fake.notes(DECK)
    assert "<img" in note["Picture"]

    media_file = _write(
        tmp_path / "media.json",
        {
            "schema": 1,
            "run_dir": str(runs),
            "language": "ja",
            "episodes": [{"run_id": "mg", "reading_file": str(mokuro), "lines": [{"unit_index": 0}]}],
        },
    )
    verdict = _api(test_home, "media", str(media_file))
    [line] = _result(runs, "mg", verdict["runs"][0]["file"])["lines"]
    assert line["picture"] and line["audio"] is None and line["location"] == "p.1", line
    with Image.open(runs / "mg" / line["picture"]) as crop:
        assert crop.size[0] < 800 and crop.size[1] < 1200  # the block's crop, not the page


def test_api_audio_episode_mines_from_the_audio(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    runs = tmp_path / "runs"
    runs.mkdir()
    audio = tmp_path / "朗読.m4a"
    _ffmpeg("-i", str(get_test_video()), "-vn", "-c:a", "aac", str(audio))

    episode = {
        "run_id": "au",
        "audio_file": str(audio),
        "subtitle_file": str(_SRT),
        "words": [{"word": "料理", "line_text": "美味しい料理を食べる"}],
    }
    verdict = _api(test_home, "mine", str(_mine(tmp_path, runs, episode)))
    [row] = _result(runs, "au", verdict["runs"][0]["file"])["words"]
    assert row["status"] == "created" and row["media_missing"] == [] and row["start"] == 5.5, row
    [note] = fake.notes(DECK)
    assert "[sound:" in note["Audio"]

    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"not audio")
    verdict = _api(test_home, "mine", str(_mine(tmp_path, runs, {**episode, "audio_file": str(bad)})))
    assert verdict["runs"][0]["error"] == "AUDIO_UNREADABLE", verdict


def test_api_inspect_lists_tracks_and_extract_copies_one(home, tmp_path) -> None:
    test_home, _fake = home
    runs = tmp_path / "runs"
    runs.mkdir()
    ass = tmp_path / "ep.ass"
    _ffmpeg("-i", str(_SRT), str(ass))
    video = tmp_path / "ep 01.mkv"
    _ffmpeg(
        "-i", str(get_test_video()), "-i", str(ass), "-map", "0", "-map", "1", "-c", "copy",
        "-metadata:s:s:0", "language=jpn", str(video),
    )  # fmt: skip

    inspect_file = _write(tmp_path / "inspect.json", {"schema": 1, "language": "ja", "sources": [{"path": str(video)}]})
    [source] = _api(test_home, "inspect", str(inspect_file))["result"]["sources"]
    assert source["kind"] == "video" and source["duration"] == pytest.approx(10.0, abs=0.2), source
    [track] = [t for t in source["tracks"] if t["kind"] == "subtitle"]
    assert (track["codec"], track["language"], track["text"], track["position"]) == ("ass", "jpn", True, 0)
    assert [t["position"] for t in source["tracks"] if t["kind"] == "audio"] == [0]

    extract_file = _write(
        tmp_path / "extract.json",
        {
            "schema": 1,
            "run_dir": str(runs),
            "language": "ja",
            "episodes": [{"run_id": "ex", "video_file": str(video), "track": track["index"]}],
        },
    )
    verdict = _api(test_home, "extract", str(extract_file))
    record = _result(runs, "ex", verdict["runs"][0]["file"])
    assert Path(record["subtitle_file"]).name == "ep 01.jpn.ass" and record["codec"] == "ass", record
    assert "美味しい料理を食べる" in Path(record["subtitle_file"]).read_text(encoding="utf-8")

    wrong = _write(
        tmp_path / "extract2.json",
        {
            "schema": 1,
            "run_dir": str(runs),
            "language": "ja",
            "episodes": [{"run_id": "ex", "video_file": str(video), "track": 99}],
        },
    )
    assert _api(test_home, "extract", str(wrong))["runs"][0]["error"] == "TRACK_NOT_FOUND"

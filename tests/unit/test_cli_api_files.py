"""--api run file and the run folder: progress, cancel file, result numbering."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from anki_miner.cli.api import files, runfolder
from anki_miner.cli.api.contract import ApiError

R = files.WordRequest


def _run_file(tmp_path: Path, **episode) -> dict:
    return {
        "schema": 1,
        "run_dir": str(tmp_path),
        "profile": None,
        "language": "ja",
        "config": {},
        "episodes": [
            {"run_id": "ep-01", "video_file": "v.mkv", "subtitle_file": "s.srt", "words": [{"word": "約束"}], **episode}
        ],
    }


def test_parse_run_file(tmp_path: Path) -> None:
    run = files.parse_run_file(_run_file(tmp_path, subtitle_offset=1, tags="job::1", audio_track_override=2))
    [ep] = run.episodes
    assert run.run_dir == tmp_path.resolve() and ep.run_id == "ep-01" and ep.tags == "job::1"
    assert ep.process_kwargs()["subtitle_offset"] == 1.0 and ep.process_kwargs()["audio_track_override"] == 2
    assert ep.words == (files.WordRequest("約束"),)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema=2),
        lambda d: d.update(bogus=1),
        lambda d: d["episodes"][0].update(run_id="../x"),
        lambda d: d["episodes"][0].update(audio_track_override=True),
        lambda d: d["episodes"][0].update(subtitle_offset="1"),
        lambda d: d["episodes"][0].update(nope=1),
        lambda d: d["episodes"].append(dict(d["episodes"][0])),  # duplicate run_id
        lambda d: d.update(episodes=[]),
        lambda d: d.update(run_dir="/definitely/not/here"),
        lambda d: d["episodes"][0].pop("words"),
        lambda d: d["episodes"][0].update(subtitle_offset=float("inf")),
        lambda d: d["episodes"][0].update(subtitle_offset=10**400),
    ],
)
def test_parse_run_file_rejects(tmp_path: Path, mutate) -> None:
    data = _run_file(tmp_path)
    mutate(data)
    with pytest.raises(ApiError) as err:
        files.parse_run_file(data)
    assert err.value.code == "BAD_RUN_FILE"


def test_subtitle_offset_left_out_or_null_is_zero(tmp_path: Path) -> None:
    for data in (_run_file(tmp_path), _run_file(tmp_path, subtitle_offset=None)):
        assert files.parse_run_file(data).episodes[0].subtitle_offset == 0.0


def test_parse_words(tmp_path: Path) -> None:
    words = [
        {"word": "約束", "line_start": 812.3},
        {"word": "今日", "line_start": 15, "line_expansion": [0, 1]},
        {"word": "言う", "line_text": "今日こそは言うよ"},
        {"word": "言う"},  # the same word twice is the run's to report, not a refusal
    ]
    [ep] = files.parse_run_file(_run_file(tmp_path, words=words)).episodes
    # R is a module-level alias (a function-local one trips ruff N806)
    assert ep.words == (R("約束", 812.3), R("今日", 15.0, None, (0, 1)), R("言う", None, "今日こそは言うよ"), R("言う"))


@pytest.mark.parametrize(
    "words",
    [
        [],
        "約束",
        [{"line_start": 1}],
        [{"word": 1}],
        [{"word": ""}],
        [{"word": "a", "line_start": -1}],
        [{"word": "a", "line_start": float("nan")}],
        [{"word": "a", "line_start": "1"}],
        [{"word": "a", "line_text": 5}],
        [{"word": "a", "line_text": " "}],
        [{"word": "a", "line_expansion": [1]}],
        [{"word": "a", "line_expansion": [0, -1]}],
        [{"word": "a", "line_expansion": [True, 0]}],
        [{"word": "a", "extra": 1}],
    ],
)
def test_parse_words_rejects(tmp_path: Path, words) -> None:
    with pytest.raises(ApiError) as err:
        files.parse_run_file(_run_file(tmp_path, words=words))
    assert err.value.code == "BAD_RUN_FILE"


def test_a_word_takes_surface_and_reading(tmp_path: Path) -> None:
    words = [{"word": "走り出す", "surface": "走り出した", "reading": "はしりだす"}]
    [request] = files.parse_run_file(_run_file(tmp_path, words=words)).episodes[0].words
    assert (request.surface, request.reading) == ("走り出した", "はしりだす")


@pytest.mark.parametrize("bad", [{"surface": ""}, {"surface": 3}, {"reading": " "}])
def test_empty_surface_or_reading_is_refused(tmp_path: Path, bad) -> None:
    with pytest.raises(ApiError) as err:
        files.parse_run_file(_run_file(tmp_path, words=[{"word": "走る", **bad}]))
    assert err.value.code == "BAD_RUN_FILE"


def test_nan_line_start_in_the_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    data = _run_file(tmp_path, words=[{"word": "a", "line_start": float("nan")}])
    path.write_text(json.dumps(data), encoding="utf-8")  # json writes (and reads) a bare NaN
    with pytest.raises(ApiError) as err:
        files.parse_run_file(files.read_json_file(path))
    assert err.value.code == "BAD_RUN_FILE"


def test_read_json_file_refuses_non_json(tmp_path: Path) -> None:
    bad = tmp_path / "run.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(ApiError):
        files.read_json_file(bad)


def test_next_result_path_counts_up(tmp_path: Path) -> None:
    assert runfolder.next_result_path(tmp_path).name == "result-1.json"
    (tmp_path / "result-1.json").touch()
    (tmp_path / "result-7.json").touch()
    assert runfolder.next_result_path(tmp_path).name == "result-8.json"


def test_write_json_is_utf8_without_bom(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    runfolder.write_json(path, {"w": "約束"})
    assert path.read_bytes().startswith(b"{") and json.loads(path.read_text(encoding="utf-8")) == {"w": "約束"}


def test_progress_file(tmp_path: Path) -> None:
    progress = runfolder.ProgressFile(tmp_path, "ep-01", min_interval=0.0)
    progress.on_stage(3, 5, "Extracting media")
    progress.on_start(26, "clips")
    progress.on_progress(12, "clip")
    assert json.loads((tmp_path / "progress.json").read_text(encoding="utf-8")) == {
        "schema": 1,
        "run_id": "ep-01",
        "stage": 3,
        "stages": 5,
        "done": 12,
        "total": 26,
    }


def test_cancel_watcher_file_cancels_and_is_deleted(tmp_path: Path) -> None:
    with runfolder.CancelWatcher(tmp_path, threading.Event(), interval=0.01) as cancel:
        (tmp_path / "cancel").touch()
        assert cancel.wait(2)
    assert not (tmp_path / "cancel").exists()


def test_cancel_watcher_signal_cancels(tmp_path: Path) -> None:
    everything = threading.Event()
    with runfolder.CancelWatcher(tmp_path, everything, interval=0.01) as cancel:
        everything.set()
        assert cancel.wait(2)


def test_cancel_watcher_uncancelled_run_leaves_no_file(tmp_path: Path) -> None:
    with runfolder.CancelWatcher(tmp_path, threading.Event(), interval=0.01) as cancel:
        time.sleep(0.05)
    assert not cancel.is_set()


@pytest.mark.parametrize(
    "words",
    [
        [{"word": "約束"}, {"word": "約束\ud83d"}],  # a string cut inside an emoji
        [{"word": "約束", "line_text": "今日\udc00"}],
    ],
)
def test_parse_run_file_refuses_unpaired_surrogates(tmp_path: Path, words) -> None:
    """json.loads accepts a lone "\\ud83d" escape, but the result file is UTF-8:
    such a word must be refused before anything is mined, not after the notes
    are added and the report cannot be written."""
    with pytest.raises(ApiError) as err:
        files.parse_run_file(_run_file(tmp_path, words=words))
    assert err.value.code == "BAD_RUN_FILE"

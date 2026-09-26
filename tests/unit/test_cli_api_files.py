"""--api run/commit files and the run folder: progress, cancel file, saved run."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from anki_miner.cli.api import files, runfolder
from anki_miner.cli.api.contract import ApiError


def _run_file(tmp_path: Path, **episode) -> dict:
    return {
        "schema": 1,
        "run_dir": str(tmp_path),
        "profile": None,
        "language": "ja",
        "config": {},
        "episodes": [{"run_id": "ep-01", "video_file": "v.mkv", "subtitle_file": "s.srt", **episode}],
    }


def test_parse_run_file(tmp_path: Path) -> None:
    run = files.parse_run_file(_run_file(tmp_path, subtitle_offset=1, tags="job::1", audio_track_override=2))
    [ep] = run.episodes
    assert run.run_dir == tmp_path.resolve() and ep.run_id == "ep-01" and ep.tags == "job::1"
    assert ep.process_kwargs()["subtitle_offset"] == 1.0 and ep.process_kwargs()["audio_track_override"] == 2


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
    ],
)
def test_parse_run_file_rejects(tmp_path: Path, mutate) -> None:
    data = _run_file(tmp_path)
    mutate(data)
    with pytest.raises(ApiError) as err:
        files.parse_run_file(data)
    assert err.value.code == "BAD_RUN_FILE"


def test_parse_commit_file(tmp_path: Path) -> None:
    data = {
        "schema": 1,
        "run_dir": str(tmp_path),
        "runs": [
            {
                "run_id": "ep-01",
                "words": [{"mined_form": "約束", "line": 57}, {"mined_form": "今日", "line_expansion": [0, 1]}],
            }
        ],
    }
    [run] = files.parse_commit_file(data).runs
    assert run.words[0] == files.WordPick("約束", 57, None)
    assert run.words[1] == files.WordPick("今日", None, (0, 1))


@pytest.mark.parametrize(
    "words",
    [
        [],
        [{"mined_form": "a"}, {"mined_form": "a"}],
        [{"mined_form": ""}],
        [{"mined_form": "a", "line": "1"}],
        [{"mined_form": "a", "line_expansion": [1]}],
        [{"mined_form": "a", "extra": 1}],
    ],
)
def test_parse_commit_file_rejects(tmp_path: Path, words) -> None:
    with pytest.raises(ApiError) as err:
        files.parse_commit_file({"schema": 1, "run_dir": str(tmp_path), "runs": [{"run_id": "r", "words": words}]})
    assert err.value.code == "BAD_RUN_FILE"


def test_read_json_file_refuses_non_json(tmp_path: Path) -> None:
    bad = tmp_path / "run.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(ApiError):
        files.read_json_file(bad)


def test_reset_run_folder_removes_only_api_files(tmp_path: Path) -> None:
    folder = tmp_path / "ep-01"
    folder.mkdir()
    for name in ("candidates.json", "prepared.json", "progress.json", "cancel", "result-1.json", "notes.txt"):
        (folder / name).write_text("x", encoding="utf-8")
    (folder / "media").mkdir()
    runfolder.reset_run_folder(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]


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


def test_saved_run_round_trip_and_staleness(tmp_path: Path) -> None:
    video, subtitle = tmp_path / "v.mkv", tmp_path / "s.srt"
    video.write_bytes(b"v")
    subtitle.write_text("1", encoding="utf-8")
    episode = files.parse_episode({"run_id": "r", "video_file": str(video), "subtitle_file": str(subtitle)}, "ep")

    def capture(view: dict) -> runfolder.SavedRun:
        return runfolder.SavedRun.capture(
            profile=None,
            language="ja",
            overlay={},
            episode=episode,
            config_view=view,
            indexes=[["dictionary", "jmdict", 1, 2]],
        )

    saved = capture({"a": 1})
    runfolder.write_json(tmp_path / "prepared.json", saved.to_json())
    loaded = runfolder.SavedRun.read(tmp_path)
    assert loaded == saved and loaded.stale_reason(saved) is None
    assert "Settings" in loaded.stale_reason(capture({"a": 2}))
    subtitle.write_text("12", encoding="utf-8")
    assert "s.srt" in loaded.stale_reason(capture({"a": 1}))

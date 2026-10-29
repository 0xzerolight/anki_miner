"""--api video-ocr over a fake scan: sources, regions, image tracks, outcomes, readiness."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.cli import api
from anki_miner.cli.api import files, settings, sources, video_ocr
from anki_miner.cli.api.contract import ApiError
from anki_miner.config import AnkiMinerConfig
from anki_miner.services.track_extractor import MediaTracks
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.region import FULL_FRAME, Region
from anki_miner.services.video_ocr.scanner import VideoOcrResult, VideoOcrStatus
from anki_miner.utils.audio_track_detector import SubtitleStream

ASS = SubtitleStream(index=2, sub_index=0, codec_name="ass", language_tag="jpn", title=None, is_text=True)
PGS = SubtitleStream(
    index=6, sub_index=1, codec_name="hdmv_pgs_subtitle", language_tag="jpn", title=None, is_text=False
)
REGION = {"x": 0.1, "y": 0.75, "w": 0.8, "h": 0.2}


class _Scan:
    """scan_video's stand-in: records its call, reports its end, then answers *status*."""

    def __init__(self, status: VideoOcrStatus = VideoOcrStatus.SUCCESS, *, detail: str = "", error=None) -> None:
        self.status, self.detail, self.error = status, detail, error
        self.calls: list[dict] = []

    def __call__(self, config, video, region, out_srt, *, progress_cb=None, cancel_event=None, bitmap_track=None):
        self.calls.append({"video": video, "region": region, "out": out_srt, "bitmap_track": bitmap_track})
        if self.error is not None:
            raise self.error
        if progress_cb is not None:
            progress_cb(30.0, None)  # length unknown: nothing to report
            progress_cb(150.0, 120.0)  # a last sample past the container length reads 100, not more
        if self.status is not VideoOcrStatus.SUCCESS:
            return VideoOcrResult(self.status, detail=self.detail)
        out_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n猫\n", encoding="utf-8")
        return VideoOcrResult(VideoOcrStatus.SUCCESS, out_srt=out_srt, cue_count=12)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(video_ocr, "require_ffmpeg", lambda config: None)
    monkeypatch.setattr(video_ocr, "video_ocr_problem", lambda config: None)
    monkeypatch.setattr(sources, "probe_tracks", lambda path, ffprobe: MediaTracks((ASS, PGS)))
    checked: list[Path] = []
    monkeypatch.setattr(video_ocr, "check_video", lambda config, video: checked.append(video))
    return checked


def _scan(monkeypatch, scan: _Scan | None = None) -> _Scan:
    scan = scan or _Scan()
    monkeypatch.setattr(video_ocr, "scan_video", scan)
    return scan


def _profile_region(monkeypatch, region: tuple[float, ...]) -> None:
    monkeypatch.setattr(
        settings, "load_profile_config", lambda profile: replace(AnkiMinerConfig(), video_ocr_region=region)
    )


def _job(tmp_path: Path, **episode) -> files.JobsFile[video_ocr.VideoOcrEpisode]:
    raw = {"run_id": "ep-1", "video_file": str(tmp_path / "ep01.mkv"), **episode}
    data = {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "episodes": [raw]}
    return files.parse_jobs_file(data, "video-ocr", video_ocr._episode)


def _run(tmp_path: Path, **episode) -> dict:
    [verdict] = video_ocr.video_ocr_runs(_job(tmp_path, **episode), threading.Event())
    return verdict


def _record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "ep-1" / "video-ocr-1.json").read_text(encoding="utf-8"))


def test_the_picture_is_read_in_the_callers_region(monkeypatch, tmp_path, fakes) -> None:
    _profile_region(monkeypatch, (0.0, 0.5, 1.0, 0.5))
    scan = _scan(monkeypatch)
    verdict = _run(tmp_path, region=REGION)
    assert verdict["ok"] and verdict["file"] == "video-ocr-1.json"
    out = tmp_path.resolve() / "ep-1" / "video-ocr-1" / "ep01.srt"
    assert _record(tmp_path) == {
        "schema": 1,
        "run_id": "ep-1",
        "subtitle_file": str(out),
        "status": "generated",
        "lines": 12,
    }
    [call] = scan.calls
    assert call["region"] == Region(0.1, 0.75, 0.8, 0.2) and call["bitmap_track"] is None and call["out"] == out
    assert fakes == [tmp_path.resolve() / "ep01.mkv"]
    progress = json.loads((tmp_path / "ep-1" / "progress.json").read_text(encoding="utf-8"))
    assert (progress["stage"], progress["stages"], progress["done"], progress["total"]) == (1, 1, 100, 100)


def test_without_a_region_the_profiles_is_used(monkeypatch, tmp_path) -> None:
    _profile_region(monkeypatch, (0.0, 0.5, 1.0, 0.5))
    scan = _scan(monkeypatch)
    assert _run(tmp_path)["ok"]
    assert scan.calls[0]["region"] == Region(0.0, 0.5, 1.0, 0.5)


def test_a_picture_with_no_region_anywhere_refuses_the_call(monkeypatch, tmp_path) -> None:
    scan = _scan(monkeypatch)
    with pytest.raises(ApiError) as caught:
        video_ocr.video_ocr_runs(_job(tmp_path), threading.Event())
    assert caught.value.code == "BAD_RUN_FILE" and "region" in caught.value.message
    assert scan.calls == [] and not (tmp_path / "ep-1").exists()


def test_an_image_track_is_read_by_its_place_among_the_subtitle_tracks(monkeypatch, tmp_path) -> None:
    scan = _scan(monkeypatch)  # no region anywhere: an image track needs none
    assert _run(tmp_path, track=6)["ok"]
    [call] = scan.calls
    assert call["bitmap_track"] == 1 and call["region"] == FULL_FRAME  # sub_index, not the stream index 6


def test_a_text_track_needs_no_ocr(monkeypatch, tmp_path) -> None:
    scan = _scan(monkeypatch)
    verdict = _run(tmp_path, track=2)
    assert verdict["error"] == "TRACK_NOT_IMAGE" and "extract" in verdict["message"] and scan.calls == []
    assert _run(tmp_path, track=9)["error"] == "TRACK_NOT_FOUND"


@pytest.mark.parametrize("name", ["movie.sup", "movie.idx"])
def test_an_image_subtitle_file_is_read_whole(monkeypatch, tmp_path, fakes, name) -> None:
    (tmp_path / name).write_bytes(b"PG")
    scan = _scan(monkeypatch)
    assert _run(tmp_path, video_file=str(tmp_path / name))["ok"]
    [call] = scan.calls
    assert call["video"] == tmp_path.resolve() / name and call["region"] == FULL_FRAME
    assert call["bitmap_track"] is None and call["out"].name == "movie.srt"
    assert fakes == []  # no video to check


def test_a_missing_image_subtitle_file_is_unreadable(monkeypatch, tmp_path) -> None:
    scan = _scan(monkeypatch)
    verdict = _run(tmp_path, video_file=str(tmp_path / "gone.sup"))
    assert verdict["error"] == "VIDEO_UNREADABLE" and scan.calls == []


@pytest.mark.parametrize(
    ("episode", "fragment"),
    [
        ({"video_file": "movie.sup", "track": 0}, "no track and no region"),
        ({"video_file": "movie.idx", "region": REGION}, "no track and no region"),
        ({"track": 6, "region": REGION}, "not both"),
        ({"region": {**REGION, "w": 0.95}}, "inside the frame"),
        ({"region": {**REGION, "h": 0}}, "fractions of the frame"),
        ({"region": {"x": 0, "y": 0, "w": 1}}, "missing: h"),
        ({"track": -1}, "track"),
    ],
)
def test_what_the_file_cannot_ask(tmp_path, episode, fragment) -> None:
    if "video_file" in episode:
        episode = {**episode, "video_file": str(tmp_path / episode["video_file"])}
    with pytest.raises(ApiError) as caught:
        _job(tmp_path, **episode)
    assert caught.value.code == "BAD_RUN_FILE" and fragment in caught.value.message


def test_no_text_is_a_finished_run_without_a_file(monkeypatch, tmp_path) -> None:
    _scan(monkeypatch, _Scan(VideoOcrStatus.NO_TEXT))
    assert _run(tmp_path, region=REGION)["ok"]
    record = _record(tmp_path)
    assert (record["subtitle_file"], record["status"], record["lines"]) == (None, "no_text", 0)


@pytest.mark.parametrize(
    ("scan", "error", "fragment"),
    [
        (_Scan(VideoOcrStatus.DECODE_FAILED, detail="moov atom not found"), "VIDEO_UNREADABLE", "moov atom"),
        (_Scan(VideoOcrStatus.CANCELLED), "CANCELLED", "cancelled"),
        (_Scan(error=EngineLoadError("onnxruntime failed to load")), "SETUP_ERROR", "onnxruntime"),
    ],
)
def test_a_scan_that_ends_early_fails_its_run(monkeypatch, tmp_path, scan, error, fragment) -> None:
    _scan(monkeypatch, scan)
    verdict = _run(tmp_path, region=REGION)
    assert verdict["error"] == error and fragment in verdict["message"]
    assert not (tmp_path / "ep-1" / "video-ocr-1").exists()


def test_video_ocr_not_ready_refuses_the_call_before_any_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        video_ocr, "video_ocr_problem", lambda config: "Video OCR's engine or models are not installed."
    )
    scan = _scan(monkeypatch)
    with pytest.raises(ApiError) as caught:
        video_ocr.video_ocr_runs(_job(tmp_path, region=REGION), threading.Event())
    assert caught.value.code == "SETUP_ERROR" and "not installed" in caught.value.message
    assert scan.calls == [] and not (tmp_path / "ep-1").exists()


def test_the_command_is_registered() -> None:
    assert "video-ocr" in api.JOB_COMMANDS and "video-ocr" in api.FEATURES
    assert api._job_call("video-ocr") is video_ocr.call

"""--api extract over a fake probe and copier: file names, records, refusals; one real ffmpeg copy."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from anki_miner.cli import api
from anki_miner.cli.api import extract, files, sources
from anki_miner.services.track_extractor import ExtractResult, ExtractStatus, MediaTracks
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream

ASS = SubtitleStream(index=2, sub_index=0, codec_name="ass", language_tag="jpn", title="日本語", is_text=True)
SRT = SubtitleStream(index=3, sub_index=1, codec_name="subrip", language_tag=None, title=None, is_text=True)
UND = SubtitleStream(index=4, sub_index=2, codec_name="subrip", language_tag="und", title=None, is_text=True)
MP4 = SubtitleStream(index=5, sub_index=3, codec_name="mov_text", language_tag="eng", title=None, is_text=True)
PGS = SubtitleStream(
    index=6, sub_index=4, codec_name="hdmv_pgs_subtitle", language_tag="jpn", title=None, is_text=False
)
AUDIO = AudioStream(1, 0, "jpn", None, "aac", 2, True)


class _Copier:
    """TrackExtractorService's stand-in: writes the planned file, or reports *status*."""

    plans: list = []

    def __init__(self, status: ExtractStatus = ExtractStatus.SAVED, reason: str = "") -> None:
        self.status, self.reason = status, reason

    def __call__(self, config):
        return self

    def extract(self, video, plan, *, cancel_event=None):
        _Copier.plans.append(plan)
        if self.status is ExtractStatus.SAVED:
            plan.dest.write_text("[Script Info]\n", encoding="utf-8")
        return ExtractResult(self.status, self.reason)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(extract, "require_ffmpeg", lambda config: None)
    monkeypatch.setattr(extract, "check_video", lambda config, video: None)
    monkeypatch.setattr(sources, "probe_tracks", lambda path, ffprobe: MediaTracks((ASS, SRT, UND, MP4, PGS), (AUDIO,)))
    _Copier.plans = []
    monkeypatch.setattr(extract, "TrackExtractorService", _Copier())


def _job(tmp_path: Path, **episode) -> files.JobsFile[extract.ExtractEpisode]:
    raw = {"run_id": "ep-1", "video_file": str(tmp_path / "ダンジョン飯 01.mkv"), "track": 2, **episode}
    data = {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "episodes": [raw]}
    return files.parse_jobs_file(data, "extract", extract._episode)


def _run(tmp_path: Path, **episode) -> dict:
    [verdict] = extract.extract_runs(_job(tmp_path, **episode), threading.Event())
    return verdict


def _record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "ep-1" / "extract-1.json").read_text(encoding="utf-8"))


def test_an_ass_track_is_saved_as_stored_under_its_language(tmp_path) -> None:
    verdict = _run(tmp_path)
    assert verdict["ok"] and verdict["file"] == "extract-1.json" and verdict["failure_is_transient"] is False
    dest = tmp_path.resolve() / "ep-1" / "extract-1" / "ダンジョン飯 01.jpn.ass"
    assert _record(tmp_path) == {
        "schema": 1,
        "run_id": "ep-1",
        "subtitle_file": str(dest),
        "language": "jpn",
        "codec": "ass",
        "warning": None,
    }
    [plan] = _Copier.plans
    # The copier maps the stream by its global index; the ref names the subtitle-only position.
    assert plan.stream == ASS and plan.ref.position == 0 and plan.fmt.muxer == "ass" and plan.fmt.codec == "copy"
    assert dest.is_file()


@pytest.mark.parametrize(
    ("track", "name", "language", "codec"),
    [
        (3, "ダンジョン飯 01.srt", None, "subrip"),  # untagged: no language part
        (4, "ダンジョン飯 01.srt", None, "subrip"),  # "und" says no more than no tag
        (5, "ダンジョン飯 01.eng.srt", "eng", "mov_text"),  # MP4 timed text becomes SubRip
    ],
)
def test_a_track_without_a_language_or_a_plain_file_format(tmp_path, track, name, language, codec) -> None:
    assert _run(tmp_path, track=track)["ok"]
    record = _record(tmp_path)
    assert Path(record["subtitle_file"]).name == name
    assert (record["language"], record["codec"]) == (language, codec)


def test_a_damaged_source_saves_with_ffmpegs_warning(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(extract, "TrackExtractorService", _Copier(reason="File ended prematurely"))
    assert _run(tmp_path)["ok"] and _record(tmp_path)["warning"] == "File ended prematurely"


def test_an_image_track_is_not_text(tmp_path) -> None:
    verdict = _run(tmp_path, track=6)
    assert verdict["error"] == "TRACK_NOT_TEXT" and "video-ocr" in verdict["message"]
    assert _Copier.plans == [] and not (tmp_path / "ep-1" / "extract-1").exists()


@pytest.mark.parametrize(("track", "fragment"), [(9, "no subtitle track 9"), (1, "audio track")])
def test_an_index_that_names_no_subtitle_track_is_not_found(tmp_path, track, fragment) -> None:
    verdict = _run(tmp_path, track=track)
    assert verdict["error"] == "TRACK_NOT_FOUND" and fragment in verdict["message"]


@pytest.mark.parametrize(
    ("status", "error"), [(ExtractStatus.FAILED, "VIDEO_UNREADABLE"), (ExtractStatus.CANCELLED, "CANCELLED")]
)
def test_a_copy_that_fails_or_is_cancelled_leaves_no_folder(monkeypatch, tmp_path, status, error) -> None:
    monkeypatch.setattr(extract, "TrackExtractorService", _Copier(status, "Invalid data found"))
    verdict = _run(tmp_path)
    assert verdict["error"] == error and not (tmp_path / "ep-1" / "extract-1").exists()
    if status is ExtractStatus.FAILED:
        assert "Invalid data found" in verdict["message"]


@pytest.mark.parametrize("track", [None, -1, "2", 1.5])
def test_track_must_be_a_stream_index(tmp_path, track) -> None:
    with pytest.raises(api.ApiError) as caught:
        _job(tmp_path, track=track)
    assert caught.value.code == "BAD_RUN_FILE" and "track" in caught.value.message


def test_the_command_is_registered() -> None:
    assert "extract" in api.JOB_COMMANDS and "tracks" in api.FEATURES
    assert api._job_call("extract") is extract.call


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg")
def test_a_real_ass_track_is_copied_as_stored(monkeypatch, tmp_path) -> None:
    monkeypatch.undo()  # the real ffprobe, check and copier, not the autouse fakes
    cue = "Dialogue: 0,0:00:00.20,0:00:00.90,Default,,0,0,0,,猫がいる"
    ass = tmp_path / "in.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1"
        "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"{cue}\n",
        encoding="utf-8",
    )
    video = tmp_path / "ダンジョン飯 01.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1"]
        + ["-i", str(ass), "-map", "0:v", "-map", "1:s", "-c:v", "ffv1", "-c:s", "copy"]
        + ["-metadata:s:s:0", "language=jpn", str(video)],
        check=True,
    )
    assert _run(tmp_path, track=1)["ok"]
    saved = Path(_record(tmp_path)["subtitle_file"])
    assert saved.name == "ダンジョン飯 01.jpn.ass" and cue in saved.read_text(encoding="utf-8")

"""--api fetch over a fake fetcher: workspaces, fetch files, refusals, transient failures."""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.cli.api import fetch, files
from anki_miner.cli.api.contract import ApiError
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    NoJapaneseSubtitlesError,
    VideoTooLongError,
    YouTubeFetchError,
    YouTubeTimeoutError,
    YtdlpNotFoundError,
)
from anki_miner.models.youtube import FetchedMedia, VideoInfo

URL = "https://www.youtube.com/watch?v=abcdefghijk"
INFO = VideoInfo(
    video_id="abcdefghijk",
    title="A video",
    duration_s=754,
    has_manual_ja_subs=True,
    has_auto_ja_subs=False,
    is_live=False,
    is_age_restricted=False,
)


class _Fetcher:
    def __init__(self, info=INFO, *, probe_error=None, fetch_error=None, wait_for_cancel=0.0, cancel_on_return=False):
        self.info, self.probe_error, self.fetch_error = info, probe_error, fetch_error
        self.wait_for_cancel = wait_for_cancel
        self.cancel_on_return = cancel_on_return  # a cancel that lands as the download ends
        self.fetches: list[tuple[str, Path, bool]] = []

    def probe_metadata(self, url):
        if self.probe_error is not None:
            raise self.probe_error
        return self.info

    def fetch_video(
        self, url, video_id, workspace, sub_mode, progress_cb=None, cancel_event=None, *, fallback_allowed=False
    ):
        self.fetches.append((sub_mode, workspace, fallback_allowed))
        if cancel_event is not None and cancel_event.wait(self.wait_for_cancel):
            raise YouTubeFetchError("Cancelled by user")
        if self.fetch_error is not None:
            raise self.fetch_error
        if progress_cb is not None:
            progress_cb("Downloading video", 0.5)
        video = workspace / f"{video_id}.mp4"
        video.write_bytes(b"video")
        if self.cancel_on_return and cancel_event is not None:
            cancel_event.set()
        if sub_mode == "transcribe":
            return FetchedMedia(video, None, "generated")
        subtitle = workspace / f"{video_id}.ja.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n今日\n", encoding="utf-8")
        return FetchedMedia(video, subtitle, "manual" if sub_mode == "manual_only" else "auto")


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(fetch, "ytdlp_available", lambda config: True)
    monkeypatch.setattr(fetch, "binary_available", lambda resolved: True)
    monkeypatch.setattr(fetch, "usable_model_installed", lambda config: True)
    monkeypatch.setattr(fetch, "MediaExtractorService", lambda config: object())


def _use(monkeypatch, fetcher: _Fetcher) -> _Fetcher:
    monkeypatch.setattr(fetch, "create_youtube_fetcher", lambda config: fetcher)
    return fetcher


def _job(tmp_path: Path, **episode) -> files.FetchFile:
    return files.parse_fetch_file(
        {
            "schema": 1,
            "run_dir": str(tmp_path),
            "language": "ja",
            "episodes": [{"run_id": "yt-1", "youtube_url": URL, **episode}],
        }
    )


def _run(tmp_path: Path, **episode) -> dict:
    [verdict] = fetch.fetch_runs(_job(tmp_path, **episode), threading.Event())
    return verdict


def _record(tmp_path: Path, n: int = 1) -> dict:
    return json.loads((tmp_path / "yt-1" / f"fetch-{n}.json").read_text(encoding="utf-8"))


def test_a_fetch_writes_its_files_and_the_overrides_mine_takes(monkeypatch, tmp_path) -> None:
    fetcher = _use(monkeypatch, _Fetcher())
    assert _run(tmp_path) == {
        "run_id": "yt-1",
        "ok": True,
        "error": None,
        "message": None,
        "file": "fetch-1.json",
        "failure_is_transient": False,
    }
    workspace = tmp_path.resolve() / "yt-1" / "fetch-1"
    assert _record(tmp_path) == {
        "schema": 1,
        "run_id": "yt-1",
        "video_file": str(workspace / "abcdefghijk.mp4"),
        "subtitle_file": str(workspace / "abcdefghijk.ja.srt"),
        "sub_source": "manual",
        "video_id": "abcdefghijk",
        "title": "A video",
        "duration": 754,
        "episode_name_override": "YT:abcdefghijk",
        "series_name_override": "YouTube",
        "source_label_override": "A video",
    }
    assert fetcher.fetches == [("manual_only", workspace, False)]
    if sys.platform != "win32":
        assert workspace.stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / "yt-1" / "media").exists()
    assert json.loads((tmp_path / "yt-1" / "progress.json").read_text(encoding="utf-8"))["stage"] == 2
    assert _run(tmp_path)["file"] == "fetch-2.json"
    assert (workspace / "abcdefghijk.mp4").exists()  # a finished fetch's files stay


def test_a_crashed_fetchs_leftover_folder_is_replaced(monkeypatch, tmp_path) -> None:
    _use(monkeypatch, _Fetcher())
    leftover = tmp_path / "yt-1" / "fetch-1"
    leftover.mkdir(parents=True)
    (leftover / "abcdefghijk.mp4.part").write_bytes(b"half")
    (tmp_path / "yt-1" / "fetch-1-old").mkdir()
    assert _run(tmp_path)["file"] == "fetch-1.json"
    assert sorted(p.name for p in leftover.iterdir()) == ["abcdefghijk.ja.srt", "abcdefghijk.mp4"]
    assert (tmp_path / "yt-1" / "fetch-1-old").is_dir()


def test_a_file_named_like_a_workspace_is_kept(monkeypatch, tmp_path) -> None:
    _use(monkeypatch, _Fetcher())
    (tmp_path / "yt-1").mkdir()
    (tmp_path / "yt-1" / "fetch-1").write_text("mine", encoding="utf-8")
    # next_numbered counts it, so the fetch takes the next number and leaves it alone.
    assert _run(tmp_path)["file"] == "fetch-2.json"
    assert (tmp_path / "yt-1" / "fetch-1").read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize(
    ("info", "probe_error", "fragment"),
    [
        (replace(INFO, is_live=True), None, "Live streams"),
        (INFO, VideoTooLongError("This video is 200 minutes long"), "200 minutes"),
        (replace(INFO, has_manual_ja_subs=False), None, "No Japanese subtitles"),
    ],
)
def test_what_video_youtube_refuses_is_youtube_refused(monkeypatch, tmp_path, info, probe_error, fragment) -> None:
    fetcher = _use(monkeypatch, _Fetcher(info, probe_error=probe_error))
    verdict = _run(tmp_path, youtube_subtitle_source="captions")
    assert verdict["error"] == "YOUTUBE_REFUSED" and fragment in verdict["message"] and verdict["file"] is None
    assert verdict["failure_is_transient"] is False
    assert fetcher.fetches == [] and not (tmp_path / "yt-1" / "fetch-1").exists()


@pytest.mark.parametrize(
    ("probe_error", "fetch_error", "transient"),
    [
        (BotDetectionError("login"), None, True),
        (None, BotDetectionError("login"), True),
        (None, CookieDatabaseLockedError("locked"), True),
        (None, YouTubeTimeoutError("timed out"), True),
        (None, YouTubeFetchError("exit 1"), False),
        (None, NoJapaneseSubtitlesError("gone"), False),
    ],
)
def test_fetch_failures_say_whether_running_again_helps(
    monkeypatch, tmp_path, probe_error, fetch_error, transient
) -> None:
    _use(monkeypatch, _Fetcher(probe_error=probe_error, fetch_error=fetch_error))
    verdict = _run(tmp_path)
    assert verdict["error"] == "FETCH_FAILED" and verdict["failure_is_transient"] is transient
    assert not (tmp_path / "yt-1" / "fetch-1").exists()


@pytest.mark.parametrize("error", [YtdlpNotFoundError("yt-dlp gone"), FfmpegNotFoundError("ffmpeg gone")])
def test_a_tool_lost_during_the_download_is_setup_error(monkeypatch, tmp_path, error) -> None:
    _use(monkeypatch, _Fetcher(fetch_error=error))
    verdict = _run(tmp_path)
    assert verdict["error"] == "SETUP_ERROR" and verdict["failure_is_transient"] is False
    assert not (tmp_path / "yt-1" / "fetch-1").exists()


def test_a_probe_failure_after_a_cancel_is_cancelled(monkeypatch, tmp_path) -> None:
    cancel = threading.Event()
    monkeypatch.setattr(fetch, "CancelWatcher", lambda folder, cancel_all: contextlib.nullcontext(cancel))

    class _Probe(_Fetcher):
        def probe_metadata(self, url):
            cancel.set()  # the probe takes no cancel: one lands while it runs
            raise YouTubeFetchError("yt-dlp metadata probe failed")

    _use(monkeypatch, _Probe())
    assert _run(tmp_path)["error"] == "CANCELLED"


def test_a_cancel_as_the_download_ends_stops_before_transcription(monkeypatch, tmp_path) -> None:
    _use(monkeypatch, _Fetcher(cancel_on_return=True))
    transcribed: list[Path] = []
    monkeypatch.setattr(fetch, "transcribe_fetched", lambda *args: transcribed.append(args[3]))
    assert _run(tmp_path, youtube_subtitle_source="transcribe")["error"] == "CANCELLED"
    assert transcribed == [] and not (tmp_path / "yt-1" / "fetch-1").exists()


def test_transcription_without_the_speech_model_is_setup_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fetch, "usable_model_installed", lambda config: False)
    fetcher = _use(monkeypatch, _Fetcher())
    verdict = _run(tmp_path, youtube_subtitle_source="transcribe")
    assert verdict["error"] == "SETUP_ERROR" and "speech model" in verdict["message"] and fetcher.fetches == []
    assert verdict["failure_is_transient"] is False


def test_a_transcribed_subtitle_is_generated(monkeypatch, tmp_path) -> None:
    _use(monkeypatch, _Fetcher())

    def fake(config, extractor, fetched, workspace, cancel_event, report):
        report("transcribing", 1.0)
        srt = workspace / "abcdefghijk.srt"
        srt.write_text("x", encoding="utf-8")
        return replace(fetched, subtitle_file=srt)

    monkeypatch.setattr(fetch, "transcribe_fetched", fake)
    assert _run(tmp_path, youtube_subtitle_source="transcribe")["ok"]
    assert _record(tmp_path)["sub_source"] == "generated"
    assert _record(tmp_path)["subtitle_file"].endswith("abcdefghijk.srt")
    assert json.loads((tmp_path / "yt-1" / "progress.json").read_text(encoding="utf-8"))["stage"] == 3


def test_alignment_follows_the_episode_over_the_profile(monkeypatch, tmp_path) -> None:
    _use(monkeypatch, _Fetcher())
    aligned: list[Path] = []

    def fake(config, fetched, workspace, cancel_event, report):
        out = workspace / "abcdefghijk.ja.retimed.srt"
        out.write_text("y", encoding="utf-8")
        aligned.append(out)
        return replace(fetched, subtitle_file=out)

    monkeypatch.setattr(fetch, "align_fetched", fake)
    assert _run(tmp_path, youtube_align_captions=True)["ok"]
    assert _record(tmp_path)["subtitle_file"] == str(aligned[0])


def test_a_cancel_file_cancels_the_run_and_is_removed(monkeypatch, tmp_path) -> None:
    class _Cancelled(_Fetcher):
        def fetch_video(self, url, video_id, workspace, sub_mode, progress_cb=None, cancel_event=None, **kw):
            (tmp_path / "yt-1" / "cancel").write_text("", encoding="utf-8")  # the caller stops it mid-download
            return super().fetch_video(url, video_id, workspace, sub_mode, progress_cb, cancel_event, **kw)

    _use(monkeypatch, _Cancelled(wait_for_cancel=5.0))
    verdict = _run(tmp_path)
    assert verdict["error"] == "CANCELLED" and not (tmp_path / "yt-1" / "cancel").exists()
    assert not (tmp_path / "yt-1" / "fetch-1").exists()


def test_a_cancel_file_left_by_an_earlier_call_is_cleared(monkeypatch, tmp_path) -> None:
    _use(monkeypatch, _Fetcher(wait_for_cancel=0.5))
    (tmp_path / "yt-1").mkdir()
    (tmp_path / "yt-1" / "cancel").write_text("", encoding="utf-8")
    assert _run(tmp_path)["ok"] is True
    assert not (tmp_path / "yt-1" / "cancel").exists()


def test_no_yt_dlp_refuses_the_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fetch, "ytdlp_available", lambda config: False)
    with pytest.raises(ApiError) as err:
        fetch.fetch_runs(_job(tmp_path), threading.Event())
    assert err.value.code == "SETUP_ERROR" and not (tmp_path / "yt-1").exists()


def test_the_fetch_command_runs_beside_an_open_window(capfd, monkeypatch, tmp_path) -> None:
    from anki_miner.cli import api, entry
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)
    ok = {
        "run_id": "yt-1",
        "ok": True,
        "error": None,
        "message": None,
        "file": "fetch-1.json",
        "failure_is_transient": False,
    }
    monkeypatch.setattr(fetch, "fetch_runs", lambda job, cancel: [ok])
    path = tmp_path / "fetch.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_dir": str(tmp_path),
                "language": "ja",
                "episodes": [{"run_id": "yt-1", "youtube_url": URL}],
            }
        ),
        encoding="utf-8",
    )
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    try:
        assert entry.main(["--api", "fetch", str(path)]) == 0
    finally:
        marker.unlock()
    [line] = capfd.readouterr().out.splitlines()
    verdict = json.loads(line)
    assert verdict["ok"] is True and verdict["command"] == "fetch" and verdict["runs"] == [ok]

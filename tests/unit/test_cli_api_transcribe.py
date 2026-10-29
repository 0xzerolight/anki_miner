"""--api transcribe over a fake generate_subtitle_one: the file, statuses, codes, progress, refusals."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from anki_miner.cli.api import transcribe
from anki_miner.cli.api.contract import AUDIO_UNREADABLE, VIDEO_UNREADABLE, ApiError
from anki_miner.exceptions import SetupError
from anki_miner.services.asr.subtitle_generation import SubtitleGenResult, SubtitleGenStatus


class _Generate:
    """generate_subtitle_one: writes the SRT unless told otherwise, and keeps what it was given."""

    def __init__(self, status: SubtitleGenStatus = SubtitleGenStatus.SUCCESS, *, wait_for_cancel: float = 0.0):
        self.status = status
        self.wait_for_cancel = wait_for_cancel
        self.calls: list[dict] = []

    def __call__(self, config, extractor, media, out_srt, **kwargs):
        self.calls.append({"config": config, "media": media, "out_srt": out_srt, **kwargs})
        kwargs["on_transcribe_start"]()
        kwargs["transcribe_progress_cb"](0.5)
        if kwargs["cancel_event"].wait(self.wait_for_cancel):
            return SubtitleGenResult(SubtitleGenStatus.CANCELLED)
        if self.status is not SubtitleGenStatus.SUCCESS:
            return SubtitleGenResult(self.status)
        kwargs["transcribe_progress_cb"](1.0)
        out_srt.parent.mkdir(parents=True, exist_ok=True)
        out_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n今日\n", encoding="utf-8")
        return SubtitleGenResult(SubtitleGenStatus.SUCCESS, out_srt)


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(transcribe, "speech_problem", lambda config: None)
    monkeypatch.setattr(transcribe, "require_ffmpeg", lambda config: None)
    monkeypatch.setattr(transcribe, "MediaExtractorService", lambda config: object())


@pytest.fixture
def checked(monkeypatch) -> list[tuple[str, Path]]:
    seen: list[tuple[str, Path]] = []
    monkeypatch.setattr(transcribe, "check_video", lambda config, path: seen.append(("video", path)))
    monkeypatch.setattr(transcribe, "check_audio", lambda config, path: seen.append(("audio", path)))
    return seen


def _use(monkeypatch, generate: _Generate) -> _Generate:
    monkeypatch.setattr(transcribe, "generate_subtitle_one", generate)
    return generate


def _file(tmp_path: Path, language: str = "ja", **episode) -> dict:
    return {
        "schema": 1,
        "run_dir": str(tmp_path),
        "language": language,
        "episodes": [{"run_id": "tr-1", **episode}],
    }


def _run(tmp_path: Path, language: str = "ja", **episode) -> dict:
    job = transcribe.files.parse_jobs_file(_file(tmp_path, language, **episode), "transcribe", transcribe._episode)
    [verdict] = transcribe.transcribe_runs(job, threading.Event())
    return verdict


def _record(tmp_path: Path, n: int = 1) -> dict:
    return json.loads((tmp_path / "tr-1" / f"transcribe-{n}.json").read_text(encoding="utf-8"))


def _progress(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "tr-1" / "progress.json").read_text(encoding="utf-8"))


def test_a_video_is_transcribed_into_the_run_folder(monkeypatch, tmp_path, checked) -> None:
    generate = _use(monkeypatch, _Generate())
    video = tmp_path / "ep 01.mkv"
    verdict = _run(tmp_path, video_file=str(video))
    assert verdict == {
        "run_id": "tr-1",
        "ok": True,
        "error": None,
        "message": None,
        "file": "transcribe-1.json",
        "failure_is_transient": False,
    }
    srt = tmp_path.resolve() / "tr-1" / "transcribe-1" / "ep 01.srt"
    assert _record(tmp_path) == {"schema": 1, "run_id": "tr-1", "subtitle_file": str(srt), "status": "generated"}
    assert srt.is_file() and checked == [("video", video.resolve())]
    [call] = generate.calls
    assert call["media"] == video.resolve() and call["language"] == "ja"
    assert call["config"].media_temp_folder == tmp_path.resolve() / "tr-1" / "media"
    assert not (tmp_path / "tr-1" / "media").exists()
    assert {k: _progress(tmp_path)[k] for k in ("stage", "stages", "done", "total")} == {
        "stage": 2,
        "stages": 2,
        "done": 100,
        "total": 100,
    }
    assert _run(tmp_path, video_file=str(video))["file"] == "transcribe-2.json"


def test_an_audio_file_is_probed_as_audio(monkeypatch, tmp_path, checked) -> None:
    _use(monkeypatch, _Generate())
    audio = tmp_path / "podcast.mp3"
    assert _run(tmp_path, audio_file=str(audio))["ok"]
    assert checked == [("audio", audio.resolve())]
    assert _record(tmp_path)["subtitle_file"].endswith("podcast.srt")


def test_no_speech_writes_no_subtitle(monkeypatch, tmp_path, checked) -> None:
    _use(monkeypatch, _Generate(SubtitleGenStatus.NO_SPEECH))
    assert _run(tmp_path, video_file=str(tmp_path / "music.mkv"))["ok"]
    assert _record(tmp_path) == {"schema": 1, "run_id": "tr-1", "subtitle_file": None, "status": "no_speech"}
    assert not (tmp_path / "tr-1" / "transcribe-1").exists()


def test_a_non_japanese_profile_transcribes_in_its_own_language(monkeypatch, tmp_path, checked) -> None:
    from anki_miner.languages.registry import get_profile

    generate = _use(monkeypatch, _Generate())
    assert _run(tmp_path, language="ko", video_file=str(tmp_path / "drama.mkv"))["ok"]
    assert generate.calls[0]["language"] == get_profile("ko").asr_language == "ko"
    assert generate.calls[0]["config"].language == "ko"


@pytest.mark.parametrize(("key", "code"), [("video_file", VIDEO_UNREADABLE), ("audio_file", AUDIO_UNREADABLE)])
def test_audio_that_cannot_be_extracted_is_the_files_unreadable_code(monkeypatch, tmp_path, checked, key, code) -> None:
    _use(monkeypatch, _Generate(SubtitleGenStatus.EXTRACTION_FAILED))
    verdict = _run(tmp_path, **{key: str(tmp_path / "broken.bin")})
    assert verdict["error"] == code and "broken.bin" in verdict["message"] and verdict["file"] is None
    assert not (tmp_path / "tr-1" / "transcribe-1.json").exists()


@pytest.mark.parametrize(
    ("key", "check", "code"),
    [("video_file", "check_video", VIDEO_UNREADABLE), ("audio_file", "check_audio", AUDIO_UNREADABLE)],
)
def test_a_file_that_does_not_open_is_refused_before_transcribing(monkeypatch, tmp_path, key, check, code) -> None:
    def unreadable(config, path):
        raise ApiError(code, f"cannot be opened: {path}")

    monkeypatch.setattr(transcribe, check, unreadable)
    generate = _use(monkeypatch, _Generate())
    assert _run(tmp_path, **{key: str(tmp_path / "x.bin")})["error"] == code
    assert generate.calls == []


def test_a_cancel_file_cancels_the_run_and_leaves_no_json(monkeypatch, tmp_path, checked) -> None:
    class _Cancelled(_Generate):
        def __call__(self, config, extractor, media, out_srt, **kwargs):
            (tmp_path / "tr-1" / "cancel").write_text("", encoding="utf-8")  # the caller stops it mid-transcription
            return super().__call__(config, extractor, media, out_srt, **kwargs)

    _use(monkeypatch, _Cancelled(wait_for_cancel=5.0))
    verdict = _run(tmp_path, video_file=str(tmp_path / "ep.mkv"))
    assert verdict["error"] == "CANCELLED" and verdict["file"] is None
    assert not (tmp_path / "tr-1" / "cancel").exists()
    assert not (tmp_path / "tr-1" / "transcribe-1.json").exists()
    assert not (tmp_path / "tr-1" / "transcribe-1").exists()


def test_a_missing_speech_model_refuses_the_call_before_any_run(monkeypatch, tmp_path, checked) -> None:
    monkeypatch.setattr(transcribe, "speech_problem", lambda config: "The speech model large-v3 is not installed.")
    generate = _use(monkeypatch, _Generate())
    job = transcribe.files.parse_jobs_file(
        _file(tmp_path, video_file=str(tmp_path / "ep.mkv")), "transcribe", transcribe._episode
    )
    with pytest.raises(ApiError) as err:
        transcribe.transcribe_runs(job, threading.Event())
    assert err.value.code == "SETUP_ERROR" and "speech model" in err.value.message
    assert generate.calls == [] and checked == [] and not (tmp_path / "tr-1").exists()


def test_a_model_that_cannot_transcribe_the_language_refuses_the_call(monkeypatch, tmp_path, checked) -> None:
    """Cantonese on small (decode_language): every run would fail, so the call is refused up front."""

    def cannot(model, language):
        raise SetupError(f"The {model} speech model cannot transcribe this language ({language}).")

    monkeypatch.setattr(transcribe, "decode_language", cannot)
    job = transcribe.files.parse_jobs_file(
        _file(tmp_path, video_file=str(tmp_path / "ep.mkv")), "transcribe", transcribe._episode
    )
    with pytest.raises(ApiError) as err:
        transcribe.transcribe_runs(job, threading.Event())
    assert err.value.code == "SETUP_ERROR" and "cannot transcribe" in err.value.message


@pytest.mark.parametrize(
    "episode",
    [
        {},
        {"video_file": "a.mkv", "audio_file": "a.mp3"},
        {"video_file": None, "audio_file": None},
        {"video_file": "a.mkv", "subtitle_file": "a.srt"},
        {"audio_file": 3},
    ],
)
def test_an_episode_names_exactly_one_file(tmp_path, episode) -> None:
    with pytest.raises(ApiError) as err:
        transcribe.files.parse_jobs_file(_file(tmp_path, **episode), "transcribe", transcribe._episode)
    assert err.value.code == "BAD_RUN_FILE"


def test_a_cached_transcript_still_reports_the_transcribing_stage(tmp_path) -> None:
    """A transcript the cache had skips the start callback: the stage opens on its one 1.0."""
    from anki_miner.cli.api.runfolder import ProgressFile

    (tmp_path / "tr-1").mkdir()
    progress = ProgressFile(tmp_path / "tr-1", "tr-1")
    progress.on_stage(1, 2, "extract")
    transcribe.transcribing_progress(progress, 2, 2)(1.0)
    assert {k: _progress(tmp_path)[k] for k in ("stage", "done", "total")} == {"stage": 2, "done": 100, "total": 100}


def test_the_transcribe_command_runs_beside_an_open_window(capfd, monkeypatch, tmp_path) -> None:
    from anki_miner.cli import api, entry
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)
    ok = {"run_id": "tr-1", "ok": True, "error": None, "message": None, "file": "transcribe-1.json"}
    monkeypatch.setattr(transcribe, "transcribe_runs", lambda job, cancel: [ok])
    path = tmp_path / "transcribe.json"
    path.write_text(json.dumps(_file(tmp_path, video_file="ep.mkv")), encoding="utf-8")
    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    try:
        assert entry.main(["--api", "transcribe", str(path)]) == 0
    finally:
        marker.unlock()
    [line] = capfd.readouterr().out.splitlines()
    verdict = json.loads(line)
    assert verdict["ok"] is True and verdict["command"] == "transcribe" and verdict["runs"] == [ok]

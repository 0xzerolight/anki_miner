"""--api assess over a fake assess_clip: the file, the rows, the shared session, progress, cancel, refusals."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from anki_miner.cli.api import assess, transcribe
from anki_miner.cli.api.contract import ApiError
from anki_miner.services.asr.clip_assessment import ClipAssessment
from anki_miner.services.asr.transcriber import Ct2ModelSession, DecodedSegment


def _found(text: str) -> ClipAssessment:
    return ClipAssessment(
        engine="ctranslate2",
        device="cuda",
        duration=2.5,
        text=text,
        detected_language="ja",
        detected_language_probability=0.97,
        language_probability=0.97,
        speech_ratio=0.6,
        speech_level_db=-24.0,
        background_level_db=-55.0,
        speech=[(0.4, 1.9)],
        segments=[DecodedSegment(0.4, 1.9, text, True, avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=0.9)],
    )


class _Assess:
    """assess_clip: a clip named broken.* has no audio; *cancel_at* names a clip whose decode a cancel interrupts."""

    def __init__(self, cancel_at: str | None = None) -> None:
        self.cancel_at = cancel_at
        self.calls: list[dict] = []

    def __call__(self, config, extractor, clip, *, language, session, cancel_event):
        self.calls.append({"config": config, "clip": clip, "language": language, "session": session})
        if clip.name == self.cancel_at:
            (config.media_temp_folder.parent / "cancel").write_text("", encoding="utf-8")
            assert cancel_event.wait(5.0)
            return None  # what a cancelled extraction gives
        return None if clip.stem == "broken" else _found(clip.stem)


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(transcribe, "speech_problem", lambda config: None)
    monkeypatch.setattr(transcribe, "require_ffmpeg", lambda config: None)
    monkeypatch.setattr(assess, "MediaExtractorService", lambda config: object())


def _use(monkeypatch, fake: _Assess) -> _Assess:
    monkeypatch.setattr(assess, "assess_clip", fake)
    return fake


def _file(tmp_path: Path, audio, language: str = "ja", **extra) -> dict:
    return {
        "schema": 1,
        "run_dir": str(tmp_path),
        "language": language,
        "episodes": [{"run_id": "as-1", "audio": audio, **extra}],
    }


def _job(data: dict):
    return assess.files.parse_jobs_file(data, "assess", assess._episode)


def _record(tmp_path: Path, n: int = 1) -> dict:
    return json.loads((tmp_path / "as-1" / f"assess-{n}.json").read_text(encoding="utf-8"))


def test_each_clip_gets_a_row_and_an_unreadable_clip_does_not_stop_the_run(monkeypatch, tmp_path) -> None:
    fake = _use(monkeypatch, _Assess())
    clips = [str(tmp_path / "今日.mp3"), str(tmp_path / "broken.mp3")]

    [verdict] = assess.assess_runs(_job(_file(tmp_path, clips)), threading.Event())

    assert verdict["ok"] and verdict["file"] == "assess-1.json"
    record = _record(tmp_path)
    assert {k: record[k] for k in ("schema", "run_id", "language")} == {"schema": 1, "run_id": "as-1", "language": "ja"}
    assert record["model"] == fake.calls[0]["config"].asr_model
    good, broken = record["clips"]
    assert good == {
        "audio_file": str((tmp_path / "今日.mp3").resolve()),
        "error": None,
        "message": None,
        "engine": "ctranslate2",
        "device": "cuda",
        "duration": 2.5,
        "text": "今日",
        "detected_language": "ja",
        "detected_language_probability": 0.97,
        "language_probability": 0.97,
        "speech_ratio": 0.6,
        "speech_level_db": -24.0,
        "background_level_db": -55.0,
        "speech": [[0.4, 1.9]],
        "segments": [
            {
                "start": 0.4,
                "end": 1.9,
                "text": "今日",
                "kept": True,
                "avg_logprob": -0.2,
                "no_speech_prob": 0.01,
                "compression_ratio": 0.9,
                "probability": None,
            }
        ],
    }
    assert broken["error"] == "AUDIO_UNREADABLE" and "broken.mp3" in broken["message"]
    assert set(broken) == set(good) and all(broken[k] is None for k in assess._MEASURED)
    progress = json.loads((tmp_path / "as-1" / "progress.json").read_text(encoding="utf-8"))
    assert {k: progress[k] for k in ("stage", "stages", "done", "total")} == {
        "stage": 1,
        "stages": 1,
        "done": 2,
        "total": 2,
    }
    assert fake.calls[0]["config"].media_temp_folder == tmp_path.resolve() / "as-1" / "media"
    assert not (tmp_path / "as-1" / "media").exists()


def test_one_model_session_serves_every_episode_and_is_let_go(monkeypatch, tmp_path) -> None:
    released: list[Ct2ModelSession] = []

    class _Session(Ct2ModelSession):
        def release(self) -> None:
            released.append(self)
            super().release()

    monkeypatch.setattr(assess, "Ct2ModelSession", _Session)
    fake = _use(monkeypatch, _Assess())
    data = _file(tmp_path, str(tmp_path / "a.mp3"))
    data["episodes"].append({"run_id": "as-2", "audio": [str(tmp_path / "b.mp3"), str(tmp_path / "c.mp3")]})

    verdicts = assess.assess_runs(_job(data), threading.Event())

    assert [v["ok"] for v in verdicts] == [True, True]
    sessions = {id(call["session"]) for call in fake.calls}
    assert len(fake.calls) == 3 and len(sessions) == 1
    assert released == [fake.calls[0]["session"]]


def test_a_cancel_inside_the_last_clip_cancels_the_run(monkeypatch, tmp_path) -> None:
    """A cancelled extraction looks like a clip with no audio: the run must say CANCELLED, not report the clip."""
    _use(monkeypatch, _Assess(cancel_at="b.mp3"))
    clips = [str(tmp_path / "a.mp3"), str(tmp_path / "b.mp3")]

    [verdict] = assess.assess_runs(_job(_file(tmp_path, clips)), threading.Event())

    assert verdict["error"] == "CANCELLED" and verdict["file"] is None
    assert not (tmp_path / "as-1" / "assess-1.json").exists()
    assert not (tmp_path / "as-1" / "cancel").exists()


def test_a_non_japanese_profile_listens_in_its_own_language(monkeypatch, tmp_path) -> None:
    fake = _use(monkeypatch, _Assess())
    assess.assess_runs(_job(_file(tmp_path, str(tmp_path / "a.mp3"), language="ko")), threading.Event())
    assert fake.calls[0]["language"] == "ko" and _record(tmp_path)["language"] == "ko"


def test_a_missing_speech_model_refuses_the_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(transcribe, "speech_problem", lambda config: "The speech model large-v3 is not installed.")
    fake = _use(monkeypatch, _Assess())
    with pytest.raises(ApiError) as err:
        assess.assess_runs(_job(_file(tmp_path, str(tmp_path / "a.mp3"))), threading.Event())
    assert err.value.code == "SETUP_ERROR" and fake.calls == []


@pytest.mark.parametrize(
    "episode",
    [
        {"run_id": "as-1"},
        {"run_id": "as-1", "audio": []},
        {"run_id": "as-1", "audio": 3},
        {"run_id": "as-1", "audio": ["a.mp3", None]},
        {"run_id": "as-1", "audio": "a.mp3", "video_file": "a.mkv"},
    ],
)
def test_an_episode_names_its_clips(tmp_path, episode) -> None:
    data = {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "episodes": [episode]}
    with pytest.raises(ApiError) as err:
        _job(data)
    assert err.value.code == "BAD_RUN_FILE"


def test_assess_is_a_job_command(capfd, monkeypatch, tmp_path) -> None:
    from anki_miner.cli import api, entry

    assert "assess" in api.JOB_COMMANDS and "assess" in api.FEATURES and "assess" in api.COMMANDS
    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)
    ok = {"run_id": "as-1", "ok": True, "error": None, "message": None, "file": "assess-1.json"}
    monkeypatch.setattr(assess, "assess_runs", lambda job, cancel: [ok])
    path = tmp_path / "assess.json"
    path.write_text(json.dumps(_file(tmp_path, ["a.mp3", "b.mp3"])), encoding="utf-8")

    assert entry.main(["--api", "assess", str(path)]) == 0

    [line] = capfd.readouterr().out.splitlines()
    verdict = json.loads(line)
    assert verdict["ok"] is True and verdict["command"] == "assess" and verdict["runs"] == [ok]

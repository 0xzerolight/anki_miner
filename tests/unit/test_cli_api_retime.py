"""--api retime over a fake retimer, and once through the real pipeline with fake engines."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pysubs2
import pytest

from anki_miner.cli import api
from anki_miner.cli.api import files, retime
from anki_miner.services import subtitle_retimer
from anki_miner.services.subtitle_retimer import RetimeOutcome
from anki_miner.services.sync_engines import SyncResult


def _write_sub(path: Path, starts: list[int]) -> Path:
    subs = pysubs2.SSAFile()
    for i, start in enumerate(starts):
        subs.events.append(pysubs2.SSAEvent(start=start, end=start + 1000, text=f"せりふ {i}"))
    subs.save(str(path), encoding="utf-8")
    return path


STARTS = [5000 + i * 3000 for i in range(14)]


class _Retimer:
    """retime_subtitle's stand-in: records its call and answers *outcome*, writing out_sub when it fits."""

    def __init__(self, outcome: RetimeOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def __call__(self, config, video, in_sub, out_sub, **kwargs) -> RetimeOutcome:
        self.calls.append({"config": config, "video": video, "in_sub": in_sub, "out_sub": out_sub, **kwargs})
        if self.outcome.ok:
            out_sub.write_text("retimed", encoding="utf-8")
        return self.outcome


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(retime, "require_ffmpeg", lambda config: None)
    monkeypatch.setattr(retime, "check_video", lambda config, video: None)


@pytest.fixture()
def pair(tmp_path) -> tuple[Path, Path]:
    video = tmp_path / "Dungeon Meshi S01E01.mkv"
    video.write_bytes(b"video")
    return video, _write_sub(tmp_path / "Dungeon Meshi S01E01.ja.srt", STARTS)


def _use(monkeypatch, outcome: RetimeOutcome) -> _Retimer:
    fake = _Retimer(outcome)
    monkeypatch.setattr(retime, "retime_subtitle", fake)
    return fake


def _job(tmp_path: Path, pair: tuple[Path, Path], **episode) -> files.JobsFile[retime.RetimeEpisode]:
    video, subtitle = pair
    raw = {"run_id": "ep-1", "video_file": str(video), "subtitle_file": str(subtitle), **episode}
    data = {"schema": 1, "run_dir": str(tmp_path), "language": "ja", "episodes": [raw]}
    return files.parse_jobs_file(data, "retime", retime._episode)


def _run(tmp_path: Path, pair: tuple[Path, Path], **episode) -> dict:
    [verdict] = retime.retime_runs(_job(tmp_path, pair, **episode), threading.Event())
    return verdict


def _record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "ep-1" / "retime-1.json").read_text(encoding="utf-8"))


def test_a_fit_against_a_reference_file_is_written_beside_the_json(monkeypatch, tmp_path, pair) -> None:
    reference = _write_sub(tmp_path / "Dungeon Meshi S01E01.en.ass", STARTS)
    fake = _use(monkeypatch, RetimeOutcome(ok=True, engine="alass", block_shifts_seconds=(-1.5, -2.0)))
    before = pair[1].read_bytes()
    verdict = _run(tmp_path, pair, reference_subtitle=str(reference))
    assert verdict["ok"] and verdict["file"] == "retime-1.json"
    out = tmp_path.resolve() / "ep-1" / "retime-1" / "Dungeon Meshi S01E01.ja.srt"
    assert _record(tmp_path) == {
        "schema": 1,
        "run_id": "ep-1",
        "subtitle_file": str(out),
        "status": "retimed",
        "engine": "alass",
        "offset_seconds": None,
        "framerate_scale": None,
        "block_shifts_seconds": [-1.5, -2.0],
        "message": None,
    }
    [call] = fake.calls
    assert call["reference_subtitle"] == reference.resolve() and call["out_sub"] == out
    assert call["in_sub"] == pair[1].resolve() and pair[1].read_bytes() == before  # the original is only read
    # An embedded reference track is extracted into the run's temp folder, removed when the run ends.
    assert call["config"].media_temp_folder == tmp_path.resolve() / "ep-1" / "media"
    assert not (tmp_path / "ep-1" / "media").exists()


@pytest.mark.parametrize(
    ("outcome", "offset", "scale", "shifts"),
    [
        (RetimeOutcome(ok=True, engine="ffsubsync", offset_seconds=-1.84, framerate_scale=1.0), -1.84, 1.0, []),
        (RetimeOutcome(ok=True, engine="alass (single offset)", block_shifts_seconds=(3.0,)), 3.0, None, [3.0]),
    ],
)
def test_one_shift_is_the_offset(monkeypatch, tmp_path, pair, outcome, offset, scale, shifts) -> None:
    fake = _use(monkeypatch, outcome)
    assert _run(tmp_path, pair)["ok"]
    record = _record(tmp_path)
    assert (record["engine"], record["offset_seconds"], record["framerate_scale"], record["block_shifts_seconds"]) == (
        outcome.engine,
        offset,
        scale,
        shifts,
    )
    assert fake.calls[0]["reference_subtitle"] is None  # the app's own chain: the video's track, then its audio


def test_no_fit_is_a_finished_run_that_says_why(monkeypatch, tmp_path, pair) -> None:
    _use(monkeypatch, RetimeOutcome(ok=False, reason="no trustworthy alignment", attempts=("ffsubsync: rejected",)))
    assert _run(tmp_path, pair)["ok"]
    assert _record(tmp_path) == {
        "schema": 1,
        "run_id": "ep-1",
        "subtitle_file": None,
        "status": "no_fit",
        "engine": None,
        "offset_seconds": None,
        "framerate_scale": None,
        "block_shifts_seconds": [],
        "message": "no trustworthy alignment",
    }


def test_a_cancelled_retime_leaves_no_folder(monkeypatch, tmp_path, pair) -> None:
    _use(monkeypatch, RetimeOutcome(ok=False, cancelled=True, reason="cancelled"))
    assert _run(tmp_path, pair)["error"] == "CANCELLED"
    assert not (tmp_path / "ep-1" / "retime-1").exists()


@pytest.mark.parametrize("which", ["subtitle_file", "reference_subtitle"])
@pytest.mark.parametrize("content", [None, b"", b"not a subtitle at all"])
def test_a_subtitle_that_cannot_be_read_is_refused(monkeypatch, tmp_path, pair, which, content) -> None:
    fake = _use(monkeypatch, RetimeOutcome(ok=True, engine="alass"))
    bad = tmp_path / "bad.srt"
    if content is not None:
        bad.write_bytes(content)
    verdict = _run(tmp_path, pair, **{which: str(bad)})
    assert verdict["error"] == "SUBTITLE_UNREADABLE" and str(bad.resolve()) in verdict["message"]
    assert fake.calls == []


def test_the_command_is_registered() -> None:
    assert "retime" in api.JOB_COMMANDS and "retime" in api.FEATURES
    assert api._job_call("retime") is retime.call


def test_a_reference_file_reaches_the_engines_as_a_subtitle_reference(monkeypatch, tmp_path, pair) -> None:
    """Through the real retimer: only the engines and the duration probe are fakes."""
    reference = _write_sub(tmp_path / "en.srt", [start - 3000 for start in STARTS])
    seen: list[tuple[Path, list[int], dict[str, Any]]] = []

    def ffsubsync(config, ref, sub, out, **kwargs) -> SyncResult:
        seen.append((ref, [e.start for e in pysubs2.load(str(ref))], kwargs))
        return SyncResult(ok=False, engine="ffsubsync", detail="no fit")

    def alass(config, ref, sub, out, **kwargs) -> SyncResult:
        seen.append((ref, [e.start for e in pysubs2.load(str(ref))], kwargs))
        subs = pysubs2.load(str(sub))
        subs.shift(ms=-3000)
        subs.save(str(out), encoding="utf-8")
        return SyncResult(ok=True, engine="alass", block_shifts_seconds=(-3.0,))

    monkeypatch.setattr(subtitle_retimer, "sync_with_ffsubsync", ffsubsync)
    monkeypatch.setattr(subtitle_retimer, "sync_with_alass", alass)
    monkeypatch.setattr(subtitle_retimer, "get_media_duration_seconds", lambda video, ffprobe: None)
    monkeypatch.setattr(subtitle_retimer, "resolve_reference", lambda *a, **k: pytest.fail("the app's chain ran"))

    assert _run(tmp_path, pair, reference_subtitle=str(reference))["ok"]
    record = _record(tmp_path)
    assert (record["status"], record["engine"], record["offset_seconds"]) == ("retimed", "alass", -3.0)
    assert [e.start for e in pysubs2.load(record["subtitle_file"])] == [start - 3000 for start in STARTS]
    [(ffs_ref, ffs_starts, _), (alass_ref, alass_starts, alass_kwargs)] = seen
    assert ffs_ref == alass_ref and ffs_starts == alass_starts == [start - 3000 for start in STARTS]
    assert alass_kwargs["sub_reference"] is True
    assert [e.start for e in pysubs2.load(str(pair[1]))] == STARTS  # the original is untouched

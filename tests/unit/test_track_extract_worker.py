"""TrackExtractWorker: per-track notes, skip rules, partial failure, fatal stop, cancel."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.gui.workers.track_extract_worker import TrackExtractWorker
from anki_miner.models.processing import TerminalOutcome
from anki_miner.services.track_extractor import ExtractResult, ExtractStatus, MediaTracks, TrackRef
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream

SUB0, SUB1, AUD0 = TrackRef("subtitle", 0), TrackRef("subtitle", 1), TrackRef("audio", 0)


def _tracks(subs: int = 1, audio: int = 1) -> MediaTracks:
    return MediaTracks(
        subtitles=tuple(SubtitleStream(i + 2, i, "ass", "jpn", None, True) for i in range(subs)),
        audio=tuple(AudioStream(i + 1, i, "jpn", None, "aac", 2, i == 0) for i in range(audio)),
    )


def _videos(tmp_path: Path, *names: str) -> list[Path]:
    paths = [tmp_path / n for n in names]
    for p in paths:
        p.write_bytes(b"video")
    return paths


def _saving_service(tracks: MediaTracks | None = None) -> MagicMock:
    service = MagicMock()
    service.probe.return_value = tracks if tracks is not None else _tracks()

    def extract(video, plan, *, cancel_event=None):
        plan.dest.write_bytes(b"track")
        return ExtractResult(ExtractStatus.SAVED)

    service.extract.side_effect = extract
    return service


class _Recorder:
    def __init__(self, worker) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []
        for name in ("file_started", "file_progress", "file_finished", "file_skipped", "file_note", "queue_finished"):
            getattr(worker, name).connect(lambda *a, _n=name: self.events.append((_n, a)))

    def of(self, kind: str):
        return [args for name, args in self.events if name == kind]

    @property
    def notes(self) -> list[str]:
        return [a[1] for a in self.of("file_note")]

    @property
    def outcome(self):
        return self.of("queue_finished")[0][0]


def _run(videos, ticked, service, **kw) -> _Recorder:
    worker = TrackExtractWorker(AnkiMinerConfig(), videos, ticked, service=service, **kw)
    rec = _Recorder(worker)
    worker.run()
    return rec


def test_two_tracks_saved_beside_the_video(tmp_path):
    (video,) = _videos(tmp_path, "EP01.mkv")
    rec = _run([video], [SUB0, AUD0], _saving_service())
    assert (tmp_path / "EP01.ass").read_bytes() == b"track"
    assert (tmp_path / "EP01.m4a").exists()
    assert rec.of("file_finished") == [(0, video, None)]
    assert any("EP01.ass" in n for n in rec.notes) and any("EP01.m4a" in n for n in rec.notes)
    assert rec.outcome is TerminalOutcome.SUCCESS


def test_custom_output_dir_is_used(tmp_path):
    (video,) = _videos(tmp_path, "EP01.mkv")
    out = tmp_path / "out"
    out.mkdir()
    _run([video], [SUB0], _saving_service(), output_dir=out)
    assert (out / "EP01.ass").exists() and not (tmp_path / "EP01.ass").exists()


def test_missing_position_is_noted_and_the_rest_saved(tmp_path):
    (video,) = _videos(tmp_path, "EP07.mkv")
    rec = _run([video], [SUB0, SUB1], _saving_service(_tracks(subs=1)))
    assert (tmp_path / "EP07.s1.jpn.ass").exists()
    assert any("Subtitle 2" in n for n in rec.notes)
    assert rec.of("file_finished") == [(0, video, None)]
    assert rec.outcome is TerminalOutcome.SUCCESS


def test_unreadable_video_is_skipped_and_the_queue_continues(tmp_path):
    broken, good = _videos(tmp_path, "EP01.mkv", "EP02.mkv")
    service = _saving_service()
    service.probe.side_effect = lambda v: MediaTracks() if v == broken else _tracks()
    rec = _run([broken, good], [SUB0], service)
    assert [a[0] for a in rec.of("file_skipped")] == [0]
    assert rec.of("file_finished") == [(1, good, None)]
    assert (tmp_path / "EP02.ass").exists()


def test_existing_output_is_skipped_without_overwrite(tmp_path):
    (video,) = _videos(tmp_path, "EP01.mkv")
    mine = tmp_path / "EP01.ass"
    mine.write_bytes(b"mine")
    service = _saving_service()
    rec = _run([video], [SUB0], service)
    assert mine.read_bytes() == b"mine"
    service.extract.assert_not_called()
    assert any("Overwrite" in n for n in rec.notes)
    assert [a[0] for a in rec.of("file_skipped")] == [0]


def test_overwrite_replaces_existing_output(tmp_path):
    (video,) = _videos(tmp_path, "EP01.mkv")
    (tmp_path / "EP01.ass").write_bytes(b"mine")
    _run([video], [SUB0], _saving_service(), overwrite=True)
    assert (tmp_path / "EP01.ass").read_bytes() == b"track"


def test_output_equal_to_the_source_is_never_written(tmp_path):
    """An .mka picked through All Files whose fallback audio name is itself."""
    (video,) = _videos(tmp_path, "EP01.mka")
    service = _saving_service(MediaTracks(audio=(AudioStream(0, 0, "jpn", None, "ac3", 2, True),)))
    rec = _run([video], [AUD0], service, overwrite=True)
    service.extract.assert_not_called()
    assert video.read_bytes() == b"video"
    assert any("video itself" in n for n in rec.notes)


def test_same_stem_videos_write_once(tmp_path):
    mkv, mp4 = _videos(tmp_path, "EP01.mkv", "EP01.mp4")
    service = _saving_service()
    rec = _run([mkv, mp4], [SUB0], service, overwrite=True)
    assert service.extract.call_count == 1
    assert any("already saved" in n for n in rec.notes)


def test_one_failed_track_fails_the_video_after_saving_the_other(tmp_path):
    (video,) = _videos(tmp_path, "EP01.mkv")
    service = _saving_service()

    def extract(v, plan, *, cancel_event=None):
        if plan.ref.kind == "audio":
            return ExtractResult(ExtractStatus.FAILED, "Invalid data found")
        plan.dest.write_bytes(b"track")
        return ExtractResult(ExtractStatus.SAVED)

    service.extract.side_effect = extract
    rec = _run([video], [SUB0, AUD0], service)
    assert (tmp_path / "EP01.ass").exists()
    ((idx, out, error),) = rec.of("file_finished")
    assert idx == 0 and out is None and "Invalid data found" in error
    assert rec.outcome is TerminalOutcome.FAILED


def test_partial_run_over_two_videos(tmp_path):
    good, bad = _videos(tmp_path, "EP01.mkv", "EP02.mkv")
    service = _saving_service()

    def extract(v, plan, *, cancel_event=None):
        if v == bad:
            return ExtractResult(ExtractStatus.FAILED, "boom")
        plan.dest.write_bytes(b"track")
        return ExtractResult(ExtractStatus.SAVED)

    service.extract.side_effect = extract
    assert _run([good, bad], [SUB0], service).outcome is TerminalOutcome.PARTIAL


def test_ffmpeg_missing_stops_the_queue(tmp_path):
    first, second = _videos(tmp_path, "EP01.mkv", "EP02.mkv")
    service = _saving_service()
    service.extract.side_effect = FfmpegNotFoundError("ffmpeg could not be started")
    worker = TrackExtractWorker(AnkiMinerConfig(), [first, second], [SUB0], service=service)
    rec = _Recorder(worker)
    worker.run()
    assert len(rec.of("file_started")) == 1
    assert isinstance(worker.fatal_exception, FfmpegNotFoundError)
    assert not worker.is_cancelled


def test_cancel_stops_without_a_finished_line(tmp_path):
    first, second = _videos(tmp_path, "EP01.mkv", "EP02.mkv")
    service = _saving_service()
    service.extract.side_effect = lambda v, plan, *, cancel_event=None: ExtractResult(ExtractStatus.CANCELLED)
    worker = TrackExtractWorker(AnkiMinerConfig(), [first, second], [SUB0], service=service)
    rec = _Recorder(worker)
    worker.cancel()
    worker.run()
    assert rec.of("file_finished") == []
    assert rec.outcome is TerminalOutcome.CANCELLED


def test_mid_track_cancel_ends_the_video_without_a_finished_line(tmp_path):
    (video,) = _videos(tmp_path, "EP01.mkv")
    service = _saving_service()
    service.extract.side_effect = lambda v, plan, *, cancel_event=None: ExtractResult(ExtractStatus.CANCELLED)
    rec = _run([video], [SUB0, AUD0], service)
    assert service.extract.call_count == 1
    assert rec.of("file_finished") == [] and rec.of("file_skipped") == []


def test_each_video_is_probed_once(tmp_path):
    videos = _videos(tmp_path, "EP01.mkv", "EP02.mkv")
    service = _saving_service()
    _run(videos, [SUB0, AUD0], service)
    assert [c.args[0] for c in service.probe.call_args_list] == videos


def test_a_save_with_an_ffmpeg_warning_says_so(tmp_path):
    (video,) = _videos(tmp_path, "EP12.mkv")
    service = _saving_service()

    def extract(v, plan, *, cancel_event=None):
        plan.dest.write_bytes(b"track")
        return ExtractResult(ExtractStatus.SAVED, "File ended prematurely")

    service.extract.side_effect = extract
    rec = _run([video], [SUB0], service)
    assert any("EP12.ass" in n and "File ended prematurely" in n for n in rec.notes)

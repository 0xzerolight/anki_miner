"""VideoOcrWorker: skip-if-exists, status → signal mapping, progress text, fatal engine errors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.gui.workers.video_ocr_worker import VideoOcrWorker
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.region import Region
from anki_miner.services.video_ocr.scanner import VideoOcrResult, VideoOcrStatus

_REGION = Region(0.1, 0.8, 0.8, 0.15)
#: The worker imports the scanner lazily (numpy stays out of app startup), so patch it at the source.
_SCAN = "anki_miner.services.video_ocr.scanner.scan_video"


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "part01.mp4"
    path.write_bytes(b"x")
    return path


def _record(worker):
    seen: dict[str, list] = {"progress": [], "finished": [], "skipped": []}
    worker.file_progress.connect(lambda *a: seen["progress"].append(a))
    worker.file_finished.connect(lambda *a: seen["finished"].append(a))
    worker.file_skipped.connect(lambda *a: seen["skipped"].append(a))
    return seen


def test_an_existing_srt_is_skipped_unless_overwrite(qtbot, test_config, video):
    (video.parent / "part01.srt").write_text("1\n")
    worker = VideoOcrWorker(test_config, [video], _REGION)
    seen = _record(worker)
    with patch(_SCAN) as scan:
        worker._process_item(0, video)
    scan.assert_not_called()
    assert len(seen["skipped"]) == 1


@pytest.mark.parametrize(
    ("status", "finished_ok", "finished_err", "skipped"),
    [
        (VideoOcrStatus.SUCCESS, 1, 0, 0),
        (VideoOcrStatus.NO_TEXT, 0, 0, 1),
        (VideoOcrStatus.DECODE_FAILED, 0, 1, 0),
        (VideoOcrStatus.CANCELLED, 0, 0, 0),
    ],
)
def test_status_maps_to_signals(qtbot, test_config, video, status, finished_ok, finished_err, skipped):
    worker = VideoOcrWorker(test_config, [video], _REGION)
    seen = _record(worker)
    result = VideoOcrResult(status, out_srt=video.with_suffix(".srt") if status is VideoOcrStatus.SUCCESS else None)
    with patch(_SCAN, return_value=result):
        worker._process_item(0, video)
    assert sum(1 for _, out, err in seen["finished"] if not err) == finished_ok
    assert sum(1 for _, out, err in seen["finished"] if err) == finished_err
    assert len(seen["skipped"]) == skipped


def test_progress_text_with_and_without_a_duration(qtbot, test_config, video):
    worker = VideoOcrWorker(test_config, [video], _REGION)
    seen = _record(worker)

    def fake_scan(*args, progress_cb, **kwargs):
        progress_cb(65.0, 3600.0)
        progress_cb(65.5, 3600.0)  # same second: not re-emitted
        progress_cb(70.0, None)
        return VideoOcrResult(VideoOcrStatus.NO_TEXT)

    with patch(_SCAN, side_effect=fake_scan):
        worker._process_item(0, video)
    messages = [m for _, _, m in seen["progress"]]
    assert len(messages) == 2
    assert "1:05" in messages[0] and "1:00:00" in messages[0]
    assert "1:10" in messages[1] and "/" not in messages[1]


def test_an_engine_load_error_stops_the_queue(qtbot, test_config, video, tmp_path):
    other = tmp_path / "part02.mp4"
    other.write_bytes(b"x")
    worker = VideoOcrWorker(test_config, [video, other], _REGION)
    with patch(_SCAN, side_effect=EngineLoadError("no runtime")) as scan:
        worker._process_queue()
    assert scan.call_count == 1
    assert worker.fatal_exception is not None

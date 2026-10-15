"""F-18: the Video OCR worker hands the chosen image track to the scan."""

from __future__ import annotations

from unittest.mock import patch

from anki_miner.gui.workers.video_ocr_worker import VideoOcrWorker
from anki_miner.services.video_ocr.region import FULL_FRAME
from anki_miner.services.video_ocr.scanner import VideoOcrResult, VideoOcrStatus

_SCAN = "anki_miner.services.video_ocr.scanner.scan_video"


def test_the_chosen_image_track_reaches_the_scan(qtbot, test_config, tmp_path):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    worker = VideoOcrWorker(test_config, [video], FULL_FRAME, bitmap_track=2)
    with patch(_SCAN, return_value=VideoOcrResult(VideoOcrStatus.NO_TEXT)) as scan:
        worker._process_item(0, video)
    assert scan.call_args.kwargs["bitmap_track"] == 2


def test_a_sup_writes_its_srt_beside_it(qtbot, test_config, tmp_path):
    sup = tmp_path / "movie.sup"
    sup.write_bytes(b"x")
    worker = VideoOcrWorker(test_config, [sup], FULL_FRAME)
    with patch(_SCAN, return_value=VideoOcrResult(VideoOcrStatus.NO_TEXT)) as scan:
        worker._process_item(0, sup)
    assert scan.call_args.args[3] == tmp_path / "movie.srt"
    assert scan.call_args.kwargs["bitmap_track"] is None

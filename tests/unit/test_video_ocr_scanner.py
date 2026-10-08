"""scan_video end to end on generated clips: real ffmpeg, a fake reader (and the real engine when opted in)."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import numpy as np
import pysubs2
import pytest

from anki_miner.services.video_ocr import scanner
from anki_miner.services.video_ocr.frame_source import Region
from anki_miner.services.video_ocr.meiki_engine import OcrBox
from anki_miner.services.video_ocr.scanner import VideoOcrStatus, scan_video
from tests.unit._video_ocr_clips import render_line, solid, write_clip

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe on PATH"
)

_LINES = {200: "一つ目の台詞", 150: "二つ目", 100: "三つ目です"}
_REGION = Region(0.0, 0.0, 1.0, 1.0)


class _ColourReader:
    """Reads a solid frame's colour as the line it encodes (0 = no text)."""

    def __init__(self, on_read=None) -> None:
        self.on_read = on_read

    def read_boxes(self, bgr: np.ndarray) -> list[OcrBox]:
        if self.on_read is not None:
            self.on_read()
        text = _LINES.get(int(bgr[2, 2, 0]), "")
        return [OcrBox(text, (0, 0, bgr.shape[1], bgr.shape[0]), 0.9)] if text else []


def _three_line_clip(path: Path) -> Path:
    fps = 20  # every duration is a whole number of frames (at 25 fps a 0.5 s blank truncates to 0.48 s)
    timeline = [(0, 1.0), (200, 2.0), (0, 0.5), (150, 1.5), (0, 0.5), (100, 2.0), (0, 1.0)]
    frames = [solid(colour, size=(320, 96)) for colour, secs in timeline for _ in range(int(secs * fps))]
    return write_clip(path, frames, fps=fps)


def test_three_timed_lines_become_three_cues(test_config, tmp_path):
    out = tmp_path / "clip.srt"
    result = scan_video(test_config, _three_line_clip(tmp_path / "clip.mkv"), _REGION, out, reader=_ColourReader())
    assert result.status is VideoOcrStatus.SUCCESS and result.cue_count == 3
    events = pysubs2.load(str(out))
    assert [e.text for e in events] == list(_LINES.values())
    for event, (start, end) in zip(events, [(1.0, 3.0), (3.5, 5.0), (5.5, 7.5)], strict=True):
        assert abs(event.start / 1000 - start) <= 0.2 and abs(event.end / 1000 - end) <= 0.2


def test_no_text_writes_no_file(test_config, tmp_path):
    out = tmp_path / "blank.srt"
    clip = write_clip(tmp_path / "blank.mkv", [solid(0, size=(320, 96))] * 50)
    assert scan_video(test_config, clip, _REGION, out, reader=_ColourReader()).status is VideoOcrStatus.NO_TEXT
    assert not out.exists()


def test_an_unknown_duration_still_scans_and_reports_elapsed_time(test_config, tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "get_media_duration_seconds", lambda *a, **k: None)
    seen: list[tuple[float, float | None]] = []
    result = scan_video(
        test_config,
        _three_line_clip(tmp_path / "c.mkv"),
        _REGION,
        tmp_path / "c.srt",
        reader=_ColourReader(),
        progress_cb=lambda t, d: seen.append((t, d)),
    )
    assert result.status is VideoOcrStatus.SUCCESS
    assert seen and all(d is None for _, d in seen)


def test_cancel_mid_scan_leaves_no_srt(test_config, tmp_path):
    out = tmp_path / "c.srt"
    event = threading.Event()
    result = scan_video(
        test_config,
        _three_line_clip(tmp_path / "c.mkv"),
        _REGION,
        out,
        reader=_ColourReader(on_read=event.set),
        cancel_event=event,
    )
    assert result.status is VideoOcrStatus.CANCELLED
    assert not out.exists()


def test_an_undecodable_file_is_decode_failed(test_config, tmp_path):
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"nope")
    result = scan_video(test_config, bad, _REGION, tmp_path / "bad.srt", reader=_ColourReader())
    assert result.status is VideoOcrStatus.DECODE_FAILED and result.detail


_MODELS = os.environ.get("ANKI_MINER_TEST_OCR_MODELS")


@pytest.mark.skipif(not _MODELS, reason="set ANKI_MINER_TEST_OCR_MODELS to a dir holding the two pinned models")
def test_real_engine_reads_rendered_lines(test_config, tmp_path):
    from anki_miner.services.video_ocr.meiki_engine import load_engine

    lines = ["こんにちは", "「行くぞ！」"]
    fps = 25
    blank = np.full((120, 640, 3), 90, dtype=np.uint8)
    frames = [blank] * fps + [render_line(lines[0])] * (2 * fps) + [blank] * fps + [render_line(lines[1])] * (2 * fps)
    clip = write_clip(tmp_path / "real.mkv", frames, fps=fps)
    out = tmp_path / "real.srt"
    reader = load_engine(Path(str(_MODELS)))
    assert scan_video(test_config, clip, _REGION, out, reader=reader).status is VideoOcrStatus.SUCCESS
    assert [e.text for e in pysubs2.load(str(out))] == lines

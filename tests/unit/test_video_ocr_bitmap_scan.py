"""F-18 end to end on generated image subtitles: a hand-written PGS .sup, alone and muxed into an MKV."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pysubs2
import pytest

from anki_miner.services.video_ocr.bitmap_source import open_bitmap_source
from anki_miner.services.video_ocr.frame_source import iter_samples
from anki_miner.services.video_ocr.region import FULL_FRAME, Region
from anki_miner.services.video_ocr.scanner import VideoOcrStatus, scan_video
from tests.unit._video_ocr_clips import write_pgs

# numpy-backed (the gate in test_asr_marker_gating.py)
pytestmark = [
    pytest.mark.asr,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe on PATH"
    ),
]

_CUES = [(1.0, 2.0), (3.0, 4.0)]
#: A region a hardsub user might have saved: it misses the box at the top-left on purpose.
_SAVED_REGION = Region(0.1, 0.8, 0.8, 0.15)


class _WhiteReader:
    """Reads the white subtitle box as one line of dialogue; the grey canvas and red video as nothing."""

    def read_text(self, bgr: np.ndarray) -> str:
        return "台詞" if int(bgr[2, 2, 0]) > 200 else ""


def _mkv_with_pgs(tmp_path: Path) -> Path:
    sup = write_pgs(tmp_path / "subs.sup", _CUES)
    plain = tmp_path / "plain.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x96:r=10:d=6",
            "-c:v",
            "ffv1",
            str(plain),
        ],
        check=True,
        timeout=120,
    )
    muxed = tmp_path / "episode.mkv"
    # -copyts: without it the muxer shifts the .sup (first cue at 1 s) to start at 0.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-copyts",
            "-i",
            str(plain),
            "-i",
            str(sup),
            "-map",
            "0",
            "-map",
            "1",
            "-c",
            "copy",
            str(muxed),
        ],
        check=True,
        timeout=120,
    )
    return muxed


def test_a_sup_is_drawn_on_the_grey_canvas_at_its_own_times(test_config, tmp_path):
    sup = write_pgs(tmp_path / "m.sup", _CUES)
    source = open_bitmap_source(test_config, sup)
    frames = list(iter_samples(test_config, sup, FULL_FRAME, fps=10, bitmap=source))
    assert 88 <= int(frames[0][1][2, 2, 0]) <= 92  # the 0x5a5a5a canvas
    lit = "".join("#" if int(frame[2, 2, 0]) > 200 else "." for _t, frame in frames)
    # One 0.1 s sample of lag is normal; the middle of each window is what is pinned.
    assert lit[:10] == "." * 10
    assert lit[12:20] == "#" * 8
    assert lit[22:30] == "." * 8
    assert lit[32:40] == "#" * 8


def test_a_sup_becomes_two_timed_cues_over_the_whole_frame(test_config, tmp_path):
    out = tmp_path / "m.srt"
    sup = write_pgs(tmp_path / "m.sup", _CUES)
    result = scan_video(test_config, sup, _SAVED_REGION, out, reader=_WhiteReader())
    assert result.status is VideoOcrStatus.SUCCESS and result.cue_count == 2
    events = pysubs2.load(str(out))
    for event, (start, end) in zip(events, _CUES, strict=True):
        assert abs(event.start / 1000 - start) <= 0.25 and abs(event.end / 1000 - end) <= 0.25


def test_an_image_track_inside_a_video_is_read_instead_of_the_picture(test_config, tmp_path):
    out = tmp_path / "episode.srt"
    result = scan_video(test_config, _mkv_with_pgs(tmp_path), _SAVED_REGION, out, reader=_WhiteReader(), bitmap_track=0)
    assert result.status is VideoOcrStatus.SUCCESS and result.cue_count == 2


def test_without_a_track_the_picture_is_read_as_before(test_config, tmp_path):
    # The red video under the track holds no text.
    out = tmp_path / "episode.srt"
    result = scan_video(test_config, _mkv_with_pgs(tmp_path), FULL_FRAME, out, reader=_WhiteReader())
    assert result.status is VideoOcrStatus.NO_TEXT


def test_a_missing_track_is_a_decode_failure(test_config, tmp_path):
    out = tmp_path / "episode.srt"
    result = scan_video(test_config, _mkv_with_pgs(tmp_path), FULL_FRAME, out, reader=_WhiteReader(), bitmap_track=5)
    assert result.status is VideoOcrStatus.DECODE_FAILED and "image subtitle track" in result.detail


def test_a_transport_stream_that_starts_late_keeps_cues_on_the_file_start_clock(test_config, tmp_path):
    """MPEG-TS/M2TS (Blu-ray) containers start above 0 s. Mining's ffmpeg -ss counts from the
    file start, as the picture path does, so the cues must too (Z.2 WB3 I-2)."""
    mkv = _mkv_with_pgs(tmp_path)
    ts = tmp_path / "episode.m2ts"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-copyts", "-i", str(mkv), "-map", "0", "-c:v", "mpeg2video"]
        + ["-c:s", "copy", "-f", "mpegts", str(ts)],
        check=True,
        timeout=120,
    )
    start = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(ts)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    assert float(start) > 0.5  # the premise: this container does not start at 0

    out = tmp_path / "episode.srt"
    result = scan_video(test_config, ts, FULL_FRAME, out, reader=_WhiteReader(), bitmap_track=0)

    assert result.status is VideoOcrStatus.SUCCESS and result.cue_count == 2
    for event, (cue_start, cue_end) in zip(pysubs2.load(str(out)), _CUES, strict=True):
        assert abs(event.start / 1000 - cue_start) <= 0.25 and abs(event.end / 1000 - cue_end) <= 0.25

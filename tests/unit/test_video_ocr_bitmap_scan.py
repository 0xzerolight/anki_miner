"""F-18 end to end on generated image subtitles: a hand-written PGS .sup, alone and muxed into an MKV."""

from __future__ import annotations

import shutil
import stat
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pysubs2
import pytest

from anki_miner.services.video_ocr.bitmap_source import open_bitmap_source
from anki_miner.services.video_ocr.frame_source import iter_samples
from anki_miner.services.video_ocr.region import FULL_FRAME, Region
from anki_miner.services.video_ocr.scanner import VideoOcrStatus, scan_video
from anki_miner.utils.audio_track_detector import list_subtitle_streams
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


def _mkv_with_pgs(tmp_path: Path, sup: Path | None = None, *, video: str = "320x96", seconds: int = 6) -> Path:
    sup = sup or write_pgs(tmp_path / "subs.sup", _CUES)
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
            f"color=c=red:s={video}:r=10:d={seconds}",
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


def _lit_rows(frames: list[tuple[float, np.ndarray]], t: float) -> list[int]:
    frame = next(f for ft, f in frames if abs(ft - t) < 0.01)
    return [int(y) for y in np.nonzero(frame.max(axis=(1, 2)) > 200)[0]]


def test_a_track_whose_size_ffprobe_misses_keeps_its_own_canvas(test_config, tmp_path):
    """A film's first cue comes after ffprobe's probe window, so the track reports no size.
    A cropped video (64 high under a 96-high track) must not become the canvas: ffmpeg would
    squash the subtitles into it, or drop the ones below its bottom edge."""
    sup = write_pgs(tmp_path / "subs.sup", [(8.0, 9.0)], box=(64, 16), at=(0, 72))
    mkv = _mkv_with_pgs(tmp_path, sup, video="320x64", seconds=10)
    assert list_subtitle_streams(mkv)[0].height is None  # the premise: the probe never saw a cue

    source = open_bitmap_source(test_config, mkv, 0)
    assert (source.width, source.height) == (320, 96)
    from_mkv = list(iter_samples(test_config, mkv, FULL_FRAME, fps=10, bitmap=source))
    from_sup = list(iter_samples(test_config, sup, FULL_FRAME, fps=10, bitmap=open_bitmap_source(test_config, sup)))
    assert _lit_rows(from_mkv, 8.5) == _lit_rows(from_sup, 8.5) == list(range(72, 88))


def test_cancel_stops_the_scan_while_ffprobe_lists_the_track_packets(test_config, tmp_path):
    """Listing a track's packets reads the whole video; on a large remux on slow storage
    that takes minutes, and Cancel must not wait for it."""
    mkv = _mkv_with_pgs(tmp_path)
    slow = tmp_path / "slow_ffprobe.sh"
    slow.write_text('#!/bin/sh\ncase "$*" in *packet=*) sleep 5;; esac\nexec ffprobe "$@"\n')
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
    config = replace(test_config, ffprobe_location=slow)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    result = scan_video(
        config, mkv, FULL_FRAME, tmp_path / "episode.srt", cancel_event=cancel, reader=_WhiteReader(), bitmap_track=0
    )

    assert result.status is VideoOcrStatus.CANCELLED
    assert time.monotonic() - started < 2

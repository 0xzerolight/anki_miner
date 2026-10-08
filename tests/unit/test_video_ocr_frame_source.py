"""frame_source: crop geometry (pure) and real-ffmpeg stills/samples on generated clips."""

from __future__ import annotations

import shutil
import subprocess
import threading

import numpy as np
import pytest

from anki_miner.exceptions import OperationCancelled
from anki_miner.services.video_ocr.frame_source import (
    MIN_REGION_PX,
    FrameSourceError,
    Region,
    crop_box,
    crop_frame,
    iter_samples,
    sample_size,
    still_at,
)
from tests.unit._video_ocr_clips import color_clip, solid, write_clip

_needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe on PATH"
)


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720), (641, 361), (360, 640)])
def test_crop_box_scales_with_resolution_and_stays_even(size):
    x, y, w, h = crop_box(Region(0.1, 0.8, 0.8, 0.15), size)
    assert w % 2 == 0 and h % 2 == 0 and x % 2 == 0 and y % 2 == 0
    assert x >= 0 and x + w <= size[0] and y >= 0 and y + h <= size[1]
    assert abs(w - 0.8 * size[0]) <= 2 and abs(h - 0.15 * size[1]) <= 2


@pytest.mark.parametrize(
    "region", [Region(0.5, 0.5, 0.0005, 0.0005), Region(0.99, 0.99, 0.5, 0.5), Region(0.0, 0.0, 1.0, 1.0)]
)
def test_tiny_and_edge_regions_stay_inside_and_usable(region):
    x, y, w, h = crop_box(region, (1280, 720))
    assert w >= MIN_REGION_PX and h >= MIN_REGION_PX
    assert x + w <= 1280 and y + h <= 720


def test_sample_size_caps_oversized_crops():
    assert sample_size(1600, 200) == (1600, 200)
    w, h = sample_size(3840, 300)
    assert w == 1920 and h % 2 == 0 and abs(h - 150) <= 2


def test_crop_frame_matches_crop_box():
    frame = np.arange(720 * 1280 * 3, dtype=np.uint32).reshape(720, 1280, 3).astype(np.uint8)
    region = Region(0.25, 0.5, 0.5, 0.25)
    x, y, w, h = crop_box(region, (1280, 720))
    assert np.array_equal(crop_frame(frame, region), frame[y : y + h, x : x + w])


def test_region_config_round_trip():
    region = Region(0.123456, 0.8, 0.75, 0.1)
    assert Region.from_config(region.as_config()) == Region(0.1235, 0.8, 0.75, 0.1)
    assert Region.from_config(()) is None


@pytest.fixture
def clip(tmp_path):
    frames = [solid(40)] * 25 + [solid(200)] * 50  # 3 s at 25 fps
    return write_clip(tmp_path / "clip.mkv", frames)


@_needs_ffmpeg
def test_still_at_returns_the_full_bgr_frame(test_config, clip):
    frame = still_at(test_config, clip, 2.0)
    assert frame.shape == (360, 640, 3)
    assert int(frame[10, 10, 0]) == 200


@_needs_ffmpeg
def test_samples_are_ten_per_second_from_zero(test_config, clip):
    samples = list(iter_samples(test_config, clip, Region(0.0, 0.5, 1.0, 0.5), fps=10))
    assert 29 <= len(samples) <= 31
    assert samples[0][0] == 0.0
    assert samples[1][0] == pytest.approx(0.1)
    assert samples[0][1].shape == (180, 640, 3)
    assert int(samples[0][1][5, 5, 0]) == 40 and int(samples[-1][1][5, 5, 0]) == 200


@_needs_ffmpeg
def test_a_late_starting_video_stream_keeps_file_relative_times(test_config, clip, tmp_path):
    # Audio from 0 s, video from 1 s; the clip's colour flips at 1.0 s of the clip = 2.0 s of the file.
    shifted = tmp_path / "shifted.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-itsoffset",
            "1",
            "-i",
            str(clip),
            "-map",
            "1:v",
            "-map",
            "0:a",
            "-t",
            "4",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            str(shifted),
        ],
        check=True,
        timeout=60,
    )
    samples = list(iter_samples(test_config, shifted, Region(0, 0, 1, 1), fps=10))
    flip_t = next(t for t, frame in samples if int(frame[5, 5, 0]) == 200)
    assert flip_t == pytest.approx(2.0, abs=0.11)


@_needs_ffmpeg
def test_odd_sized_source_pixels_match_between_still_and_scan(test_config, tmp_path):
    gradient = np.tile((np.arange(641) % 256).astype(np.uint8)[None, :, None], (361, 1, 3))
    clip = write_clip(tmp_path / "odd.mkv", [gradient] * 10)
    region = Region(0.3, 0.55, 0.5, 0.3)
    still = still_at(test_config, clip, 0.0)
    sample = next(iter(iter_samples(test_config, clip, region, fps=10)))[1]
    assert still.shape[:2] == (361, 641)
    assert np.array_equal(sample, crop_frame(still, region))


@_needs_ffmpeg
def test_rotation_metadata_geometry_and_pixels_match_between_still_and_scan(test_config, tmp_path):
    base = tmp_path / "base.mp4"
    # Static and intra-only (-g 1): the fps filter's sample 0 is source frame 1, so an
    # animated source would compare two different frames.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "smptebars=s=642x362:r=25:d=1",
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            "-g",
            "1",
            str(base),
        ],
        check=True,
        timeout=60,
    )
    rotated = tmp_path / "rotated.mp4"
    # Stream copy keeps the rotation as metadata (a transcode would bake it into the pixels).
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(base), "-c", "copy", str(rotated)],
        capture_output=True,
        timeout=60,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream_side_data=rotation",
            "-of",
            "csv=p=0",
            str(rotated),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode != 0 or not probe.stdout.strip():
        pytest.skip("this ffmpeg cannot write rotation metadata")
    region = Region(0.2, 0.6, 0.6, 0.3)
    still = still_at(test_config, rotated, 0.0)
    sample = next(iter(iter_samples(test_config, rotated, region, fps=10)))[1]
    assert still.shape[:2] == (642, 362)
    # Exact: an odd crop offset on 4:2:0 video shifts chroma (max diff ~226), so no tolerance.
    assert np.array_equal(sample, crop_frame(still, region))


@_needs_ffmpeg
def test_cancel_stops_decoding_and_raises(test_config, tmp_path):
    long_clip = color_clip(tmp_path / "long.mkv", seconds=30)
    event = threading.Event()
    got = 0
    with pytest.raises(OperationCancelled):
        for _t, _frame in iter_samples(test_config, long_clip, Region(0, 0, 1, 1), fps=10, cancel_event=event):
            got += 1
            if got == 3:
                event.set()
    assert got < 300


@_needs_ffmpeg
def test_an_undecodable_file_raises_frame_source_error(test_config, tmp_path):
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"not a video")
    with pytest.raises(FrameSourceError):
        still_at(test_config, bad, 0.0)

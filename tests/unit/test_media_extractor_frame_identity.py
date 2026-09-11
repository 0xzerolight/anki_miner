"""Real-ffmpeg proof that a picked second extracts the frame the user saw.

Everything else in the screenshot path is asserted against a mocked Popen, so
nothing pinned the one fact the word curator's frame pick rests on: the instant
mpv reports for the frame on screen is the instant ``ffmpeg -ss`` hands back
that same frame. Both resolve a timestamp to the first frame whose pts is at or
after it, which is also why quantizing the instant to whole milliseconds cannot
slip to the frame before.

These pass with or without the curator fix -- the extractor side never changed.
They are the floor underneath it: an ffmpeg release that started snapping to
keyframes, or a reformatted ``-ss`` value, turns them red instead of silently
shipping the wrong frame on every card.

Skipped where ffmpeg/ffprobe are absent (CI's runner installs neither), and the
fixture clip is read only -- never ``get_test_video()``, which would regenerate
it into the tree.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from anki_miner.models import TokenizedWord
from anki_miner.services.media_extractor import MediaExtractorService
from tests.e2e.fixtures_media import TEST_VIDEO_PATH

#: The fixture clip: h264, 15 fps, 150 frames, 10 s, container start_time 0.
FPS = 15

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None or not TEST_VIDEO_PATH.exists(),
    reason="needs ffmpeg, ffprobe and the e2e fixture clip",
)


def _frame_digest(path: Path) -> str:
    """Hash the DECODED pixels, so JPEG container or metadata drift cannot lie."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    digests = [line.split(",")[-1].strip() for line in proc.stdout.splitlines() if not line.startswith("#")]
    assert digests, proc.stdout
    return digests[0]


def _reference_frame(index: int, out: Path) -> Path:
    """The clip's frame ``index``, selected by NUMBER so no seek is involved."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(TEST_VIDEO_PATH),
            "-vf",
            f"select=eq(n\\,{index})",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.mark.parametrize(
    ("target", "expected_frame"),
    [
        (2.0, 30),  # exactly a frame's own pts
        (2.066, 31),  # frame 31's pts (2.0666...) truncated to whole ms, as the player reports it
        (2.03, 31),  # between two frames
    ],
)
def test_input_seek_takes_the_first_frame_at_or_after_the_target(target, expected_frame):
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-copyts",
            "-ss",
            str(target),
            "-i",
            str(TEST_VIDEO_PATH),
            "-frames:v",
            "1",
            "-vf",
            "showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"pts_time:([0-9.]+)", proc.stderr)
    assert match, proc.stderr
    assert float(match.group(1)) == pytest.approx(expected_frame / FPS, abs=1e-3)


def test_a_stamped_second_extracts_the_frame_the_viewer_saw(test_config, tmp_path):
    """frame 31, not 30: the off-by-one a naive rounding would have produced."""
    word = TokenizedWord(
        surface="食べた",
        lemma="食べる",
        reading="タベル",
        sentence="食べるのテスト",
        start_time=2.0,
        end_time=4.0,
        duration=2.0,
        pos="動詞",
        screenshot_override=2.066,  # what the player reports for frame 31
    )
    service = MediaExtractorService(
        dataclasses.replace(test_config, ffmpeg_location=Path(shutil.which("ffmpeg") or "ffmpeg"))
    )
    shot = tmp_path / "shot.jpg"
    assert service._extract_screenshot(
        TEST_VIDEO_PATH, word.start_time, word.duration, shot, None, screenshot_time=word.screenshot_override
    )

    got = _frame_digest(shot)
    assert got == _frame_digest(_reference_frame(31, tmp_path / "f31.jpg"))
    assert got != _frame_digest(_reference_frame(30, tmp_path / "f30.jpg"))


def test_the_jpeg_is_not_empty(test_config, tmp_path):
    """A zero-byte still would make every digest comparison above vacuous."""
    service = MediaExtractorService(
        dataclasses.replace(test_config, ffmpeg_location=Path(shutil.which("ffmpeg") or "ffmpeg"))
    )
    shot = tmp_path / "shot.jpg"
    assert service._extract_screenshot(TEST_VIDEO_PATH, 2.0, 2.0, shot, None, screenshot_time=2.066)
    assert shot.stat().st_size > 0
    assert hashlib.sha256(shot.read_bytes()).hexdigest()

"""Deterministic, copyright-free test video for the E2E harness.

The harness needs a real video file with BOTH a video and an audio stream so
the full mining pipeline (screenshot + sentence-audio extraction) has something
to chew on, without ever touching copyrighted media. We synthesize one from
ffmpeg's built-in ``lavfi`` sources: a ``testsrc`` colour pattern plus a
``sine`` tone. The clip is committed to the repo (it is tiny) and regenerated
on demand when missing.

ffmpeg/ffprobe are resolved through :mod:`anki_miner.utils.ffmpeg_resolver` so a
bundled-binary override is honoured exactly as the production code path does;
absent an override the bare ``"ffmpeg"``/``"ffprobe"`` literals are used (PATH).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe

__all__ = [
    "ASSETS_DIR",
    "TEST_VIDEO_PATH",
    "generate_test_video",
    "get_test_video",
]

#: Directory holding the committed, self-contained E2E input assets.
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
#: The committed clip. Tiny (320x240 / 15fps / 10s / x264 ~ a few dozen KB).
TEST_VIDEO_PATH = ASSETS_DIR / "e2e_clip.mp4"

# Clip geometry/timing. Kept small on purpose so the committed asset stays tiny;
# 10s comfortably covers the subtitle timings in ``fixtures_subtitle``.
_SIZE = "320x240"
_RATE = 15
_DURATION = 10


def _ffmpeg() -> str:
    """Resolve the ffmpeg executable, honouring a bundled-binary override."""
    return resolve_ffmpeg(AnkiMinerConfig())


def _ffprobe() -> str:
    """Resolve the ffprobe executable, honouring a bundled-binary override."""
    return resolve_ffprobe(AnkiMinerConfig())


def _probe_streams(path: Path) -> tuple[bool, bool]:
    """Return ``(has_video, has_audio)`` for *path* via ffprobe.

    Inspects ``-show_streams`` output and looks for ``codec_type=video`` /
    ``codec_type=audio`` lines (the flat default-format key=value output is
    locale-stable and needs no JSON parsing).

    Raises:
        RuntimeError: If ffprobe is unavailable (callers/tests skip on this).
        subprocess.CalledProcessError: If ffprobe runs but fails on *path*.
    """
    try:
        completed = subprocess.run(
            [
                _ffprobe(),
                "-v",
                "error",
                "-show_streams",
                "-of",
                "default=noprint_wrappers=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:  # ffprobe not on PATH / not bundled
        raise RuntimeError(f"ffprobe not available ({e}); cannot probe streams of {path}") from e
    codec_types = {line.split("=", 1)[1] for line in completed.stdout.splitlines() if line.startswith("codec_type=")}
    return ("video" in codec_types, "audio" in codec_types)


def generate_test_video(path: Path) -> Path:
    """Synthesize a ~10s clip with a real video AND audio stream at *path*.

    Uses ffmpeg's built-in ``lavfi`` sources (no copyrighted input):
    a ``testsrc`` colour pattern and a 440 Hz ``sine`` tone, muxed to H.264 +
    AAC. Idempotent: if *path* already exists and ffprobe confirms both a video
    and an audio stream, regeneration is skipped and the existing file returned.

    Args:
        path: Destination ``.mp4`` path (parent dirs created as needed).

    Returns:
        ``path`` (for chaining).

    Raises:
        RuntimeError: If ffprobe is unavailable during the idempotency/verify
            checks.
        subprocess.CalledProcessError: If the ffmpeg encode fails.
    """
    if path.exists():
        has_video, has_audio = _probe_streams(path)
        if has_video and has_audio:
            return path

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={_SIZE}:rate={_RATE}:duration={_DURATION}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={_DURATION}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def get_test_video() -> Path:
    """Return the committed clip path, regenerating it if missing.

    Returns:
        Path to ``tests/e2e/assets/e2e_clip.mp4``. When the asset is absent and
        ffmpeg is available it is regenerated in place; if ffmpeg is missing the
        regeneration attempt raises (callers should skip in that case).
    """
    if not TEST_VIDEO_PATH.exists():
        generate_test_video(TEST_VIDEO_PATH)
    return TEST_VIDEO_PATH

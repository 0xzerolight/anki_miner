"""Shared ffmpeg/ffprobe helpers for the demo-asset generation scripts.

Used by ``make_card_gifs.py``. Part of the optional GIF tooling, not the shipped
``anki_miner`` package -- nothing here is imported at runtime by the app.
ffmpeg/ffprobe must be on PATH (already a project requirement).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class FfmpegError(RuntimeError):
    """Raised when ffmpeg/ffprobe is missing or a command fails."""


def ensure_tools() -> None:
    """Verify ffmpeg and ffprobe are on PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FfmpegError(f"{tool} not found on PATH. Install ffmpeg (see project README) and retry.")


def run(cmd: list[str]) -> None:
    """Run a command, raising FfmpegError with captured stderr on failure."""
    logger.debug("run: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FfmpegError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")


def probe(path: Path) -> tuple[float, int]:
    """Return (duration_seconds, frame_count) for a video file via ffprobe."""
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe failed on {path}: {proc.stderr.strip()}")
    stream = json.loads(proc.stdout)["streams"][0]
    duration = float(stream.get("duration") or 0.0)
    frames = int(stream.get("nb_read_frames") or 0)
    return duration, frames


def encode_gif(src: Path, out: Path, fps: int, width: int, max_colors: int = 256) -> None:
    """Encode ``src`` video to an optimized palette GIF.

    Two-pass palettegen/paletteuse: a full-stats palette of ``max_colors`` with
    ordered bayer dithering (bayer_scale=3). Bayer is chosen over error-diffusion
    (sierra2_4a) deliberately: on photographic anime frames sierra's noise roughly
    DOUBLES the GIF size (~5MB vs ~2.8MB) for little visible gain, whereas bayer
    stays smooth and compresses well. diff_mode=rectangle keeps static card text
    cheap. Lowering ``max_colors`` trims size further.
    """
    palette = out.with_suffix(".palette.png")
    scale = f"scale={width}:-1:flags=lanczos"
    try:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                f"fps={fps},{scale},palettegen=stats_mode=full:max_colors={max_colors}",
                str(palette),
            ]
        )
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-i",
                str(palette),
                "-lavfi",
                f"fps={fps},{scale}[x];" "[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
                str(out),
            ]
        )
    finally:
        palette.unlink(missing_ok=True)


def encode_mp4(
    src: Path,
    out: Path,
    crf: int = 20,
    audio_path: Path | None = None,
    audio_delay_secs: float = 0.0,
) -> None:
    """Encode ``src`` to a web/GitHub-playable h264 MP4.

    With ``audio_path`` the track is muxed in (AAC), delayed by
    ``audio_delay_secs`` so it starts on the back reveal. The video's frame 0 is
    the AVIF scene start (see make_card_gifs.record_side), so audio at delay 0
    plays IN PHASE with the first animation loop. The audio is padded with
    trailing silence (``apad``) so the finite video stream is the shortest one:
    ``-shortest`` then locks the output to the full video length. After the
    sentence ends the screenshot keeps looping cleanly to the fixed end;
    longer-than-video audio is trimmed at the video end. Without ``audio_path``
    the MP4 is silent (``-an``). ``-dn`` drops any stray data track so the file
    is video+audio only.
    """
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if audio_path is not None:
        delay_ms = int(round(audio_delay_secs * 1000))
        cmd += [
            "-i",
            str(audio_path),
            "-filter_complex",
            f"[1:a]adelay={delay_ms}|{delay_ms},apad[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-dn",
        "-crf",
        str(crf),
    ]
    if audio_path is not None:
        cmd += ["-shortest"]
    cmd += [str(out)]
    run(cmd)


def human_size(path: Path) -> str:
    """Human-readable file size for logging."""
    n = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}GB"

"""F-02 against real ffmpeg: a normalised clip is 48 kHz mono, keeps its window and comes out louder."""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from anki_miner.services.media_extractor import MediaExtractorService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe on PATH"
)


def _quiet_source(path: Path) -> Path:
    """Twelve seconds of a quiet stereo tone in Matroska/FLAC, like a ripped episode's audio."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:r=44100:d=12",
            "-af",
            "volume=0.05",
            "-ac",
            "2",
            "-c:a",
            "flac",
            str(path),
        ],
        check=True,
    )
    return path


def _probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=sample_rate,channels:format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return int(values["sample_rate"]), int(values["channels"]), float(values["duration"])


def _mean_volume_db(path: Path) -> float:
    err = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    match = re.search(r"mean_volume: (-?[\d.]+) dB", err)
    assert match is not None, err
    return float(match.group(1))


@pytest.mark.parametrize(("audio_format", "encoder"), [("mp3", "libmp3lame"), ("opus", "libopus")])
def test_a_normalised_clip_is_48k_mono_full_length_and_louder(test_config, tmp_path, audio_format, encoder):
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if f" {encoder} " not in encoders:
        pytest.skip(f"this ffmpeg has no {encoder} encoder")
    source = _quiet_source(tmp_path / "ep.mkv")
    service = MediaExtractorService(
        dataclasses.replace(test_config, normalize_audio_loudness=True, audio_format=audio_format)
    )
    out = tmp_path / f"clip.{audio_format}"

    assert service._extract_audio(source, 4.7, 2.6, out) is True

    rate, channels, duration = _probe(out)
    assert (rate, channels) == (48000, 1)
    assert duration == pytest.approx(2.6, abs=0.05)
    assert _mean_volume_db(out) > _mean_volume_db(source) + 6.0

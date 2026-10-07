"""Utilities → Tracks against real ffmpeg: every codec row the table names, round-tripped.

Sources are generated per test (lavfi sine audio, plus a small cue file for
subtitle rows). Rows nothing can generate stay unit-only (argv checked in
test_track_extractor.py): PGS and DVD/DVB need a bitmap-subtitle encoder that
takes text, and no demuxer produces codec ``ssa``.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.track_extractor import ExtractStatus, TrackExtractorService, TrackRef, plan_outputs
from anki_miner.utils.file_pairing import find_sibling_subtitle

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe on PATH"
)

CUE = "猫がいる"
_ASS = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 384\nPlayResY: 288\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    f"Dialogue: 0,0:00:00.20,0:00:00.90,Default,,0,0,0,,{CUE}\n"
)
_CUES = {
    "ass": _ASS,
    "srt": f"1\n00:00:00,200 --> 00:00:00,900\n{CUE}\n",
    "vtt": f"WEBVTT\n\n00:00:00.200 --> 00:00:00.900\n{CUE}\n",
}
#: The audio each container carries under a subtitle row.
_CONTAINER_AUDIO = {"mkv": "flac", "webm": "libopus", "mp4": "aac"}


@pytest.fixture(scope="module")
def encoders() -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True).stdout


def _need(encoders: str, *names: str) -> None:
    for name in names:
        if name != "copy" and f" {name} " not in encoders:
            pytest.skip(f"this ffmpeg has no {name} encoder")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def _sine() -> list[str]:
    return ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1"]


def _codec(path: Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.strip().splitlines()[0]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract_one(src: Path, ref: TrackRef) -> Path:
    service = TrackExtractorService(AnkiMinerConfig())
    tracks = service.probe(src)
    planned, missing = plan_outputs(src, tracks, [ref], src.parent)
    assert missing == [] and len(planned) == 1
    before = _sha(src)
    result = service.extract(src, planned[0])
    assert result.status is ExtractStatus.SAVED, result.reason
    # A clean source copies silently at -v error; anything here would put a
    # false "ffmpeg reported" warning on every save.
    assert result.reason == ""
    assert _sha(src) == before
    dest = planned[0].dest
    assert dest.stat().st_size > 0
    return dest


@pytest.mark.parametrize(
    ("encoder", "container", "suffix", "codec"),
    [
        ("aac", "mkv", ".m4a", "aac"),
        ("alac", "mkv", ".m4a", "alac"),
        ("libopus", "mkv", ".opus", "opus"),
        ("libopus", "webm", ".opus", "opus"),
        ("libvorbis", "mkv", ".ogg", "vorbis"),
        ("flac", "mkv", ".flac", "flac"),
        ("libmp3lame", "mkv", ".mp3", "mp3"),
        ("ac3", "mkv", ".mka", "ac3"),
        ("eac3", "mkv", ".mka", "eac3"),
        ("pcm_s16le", "mkv", ".mka", "pcm_s16le"),
    ],
)
def test_audio_row_round_trips(tmp_path, encoders, encoder, container, suffix, codec):
    _need(encoders, encoder)
    src = tmp_path / f"EP01.{container}"
    _ffmpeg(*_sine(), "-c:a", encoder, str(src))
    dest = _extract_one(src, TrackRef("audio", 0))
    assert dest.name == f"EP01{suffix}"
    assert _codec(dest) == codec


@pytest.mark.parametrize(
    ("cue", "sub_encoder", "container", "suffix", "codec"),
    [
        ("ass", "copy", "mkv", ".ass", "ass"),
        ("srt", "copy", "mkv", ".srt", "subrip"),
        ("vtt", "copy", "mkv", ".vtt", "webvtt"),
        ("vtt", "copy", "webm", ".vtt", "webvtt"),
        ("srt", "mov_text", "mp4", ".srt", "subrip"),
    ],
)
def test_subtitle_row_round_trips(tmp_path, encoders, cue, sub_encoder, container, suffix, codec):
    audio = _CONTAINER_AUDIO[container]
    _need(encoders, audio, sub_encoder)
    cues = tmp_path / f"cues.{cue}"
    cues.write_text(_CUES[cue], encoding="utf-8")
    src = tmp_path / f"EP01.{container}"
    _ffmpeg(
        *_sine(),
        "-i",
        str(cues),
        "-map",
        "0:a",
        "-map",
        "1:s",
        "-c:a",
        audio,
        "-c:s",
        sub_encoder,
        "-metadata:s:s:0",
        "language=jpn",
        str(src),
    )
    dest = _extract_one(src, TrackRef("subtitle", 0))
    assert dest.name == f"EP01{suffix}"
    assert _codec(dest) == codec
    text = dest.read_text(encoding="utf-8")
    assert CUE in text
    if suffix == ".ass":
        assert "[Script Info]" in text and "Dialogue:" in text


def _multi_track(tmp_path: Path, stem: str) -> Path:
    for ext in ("ass", "srt"):
        (tmp_path / f"cues.{ext}").write_text(_CUES[ext], encoding="utf-8")
    src = tmp_path / f"{stem}.mkv"
    _ffmpeg(
        *_sine(),
        *_sine(),
        "-i",
        str(tmp_path / "cues.ass"),
        "-i",
        str(tmp_path / "cues.srt"),
        "-map",
        "0:a",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-map",
        "3:s",
        "-c:a:0",
        "aac",
        "-c:a:1",
        "flac",
        "-c:s",
        "copy",
        "-metadata:s:s:0",
        "language=jpn",
        "-metadata:s:s:1",
        "language=eng",
        str(src),
    )
    return src


def test_several_ticked_tracks_get_distinct_names(tmp_path, encoders):
    _need(encoders, "aac", "flac")
    src = _multi_track(tmp_path, "EP01")
    service = TrackExtractorService(AnkiMinerConfig())
    ticked = [TrackRef("subtitle", 0), TrackRef("subtitle", 1), TrackRef("audio", 0), TrackRef("audio", 1)]
    planned, missing = plan_outputs(src, service.probe(src), ticked, tmp_path)
    assert missing == []
    for plan in planned:
        assert service.extract(src, plan).status is ExtractStatus.SAVED
    assert sorted(p.dest.name for p in planned) == [
        "EP01.a1.m4a",
        "EP01.a2.flac",
        "EP01.s1.jpn.ass",
        "EP01.s2.eng.srt",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="':' is not a legal file-name character on Windows")
def test_unicode_and_colon_stem_round_trips_and_pairs(tmp_path, encoders):
    _need(encoders, "aac", "flac")
    src = _multi_track(tmp_path, "Re:Zero 日本語 01")
    dest = _extract_one(src, TrackRef("subtitle", 0))
    assert dest.name == "Re:Zero 日本語 01.ass"
    assert find_sibling_subtitle(src) == dest


def test_a_truncated_source_saves_with_ffmpegs_warning(tmp_path, encoders):
    """Half a download: ffmpeg exits 0 but says so, and the tool must pass that on."""
    _need(encoders, "flac")
    cues = tmp_path / "cues.srt"
    cues.write_text(
        "".join(f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},500\n{CUE}\n\n" for n in range(1, 30)), encoding="utf-8"
    )
    full = tmp_path / "full.mkv"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=30",
        "-i",
        str(cues),
        "-map",
        "0:a",
        "-map",
        "1:s",
        "-c:a",
        "flac",
        "-c:s",
        "copy",
        str(full),
    )
    src = tmp_path / "EP01.mkv"
    src.write_bytes(full.read_bytes()[: full.stat().st_size // 2])
    service = TrackExtractorService(AnkiMinerConfig())
    planned, _ = plan_outputs(src, service.probe(src), [TrackRef("subtitle", 0)], tmp_path)
    result = service.extract(src, planned[0])
    assert result.status is ExtractStatus.SAVED
    assert result.reason

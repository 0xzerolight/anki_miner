"""F-02 + CC-07 (R1): the sentence-clip cut normalises loudness and fades its ends, behind one switch."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.media_extractor import (
    CLIP_FADE_SECONDS,
    LOUDNORM_FILTER,
    LOUDNORM_SAMPLE_RATE,
    MediaExtractorService,
    clip_audio_filter,
)
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg

MODULE = "anki_miner.services.media_extractor"


def _popen_mock() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate.return_value = ("", "")
    return proc


def _service(test_config, **overrides) -> MediaExtractorService:
    with patch(f"{MODULE}.ensure_directory"):
        svc = MediaExtractorService(dataclasses.replace(test_config, **overrides))
    svc._animated_encoder_ok.update(libmp3lame=True, libopus=True)
    return svc


def _cut(svc: MediaExtractorService, tmp_path: Path) -> list[str]:
    out = tmp_path / f"clip.{svc.config.audio_format}"
    out.write_bytes(b"x")
    with (
        patch(f"{MODULE}.subprocess.Popen", return_value=_popen_mock()) as popen,
        patch.object(svc, "_get_japanese_audio_stream", return_value=None),
    ):
        assert svc._extract_audio(tmp_path / "ep.mkv", 4.7, 2.6, out) is True
    return popen.call_args[0][0]


def test_the_filter_chain_normalises_then_fades_both_ends():
    assert LOUDNORM_FILTER == "loudnorm=I=-16:TP=-1.5:LRA=11"
    assert CLIP_FADE_SECONDS == 0.03
    assert clip_audio_filter(2.6) == "loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.03,afade=t=out:st=2.570:d=0.03"


def test_a_clip_shorter_than_the_fade_starts_its_fade_out_at_zero():
    assert clip_audio_filter(0.02).endswith(",afade=t=out:st=0.000:d=0.03")


@pytest.mark.parametrize("audio_format", ["mp3", "opus"])
def test_switch_on_adds_the_chain_and_an_explicit_rate(test_config, tmp_path, audio_format):
    cmd = _cut(_service(test_config, normalize_audio_loudness=True, audio_format=audio_format), tmp_path)

    assert cmd[cmd.index("-af") + 1] == clip_audio_filter(2.6)
    assert cmd[cmd.index("-ar") + 1] == LOUDNORM_SAMPLE_RATE == "48000"
    # Mono stays, before the filter; the output file stays last.
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd.index("-ac") < cmd.index("-af")
    assert cmd[-1] == str(tmp_path / f"clip.{audio_format}")


@pytest.mark.parametrize(("audio_format", "encoder"), [("mp3", "libmp3lame"), ("opus", "libopus")])
def test_switch_off_keeps_the_clip_command_byte_identical(test_config, tmp_path, audio_format, encoder):
    svc = _service(test_config, normalize_audio_loudness=False, audio_format=audio_format, audio_bitrate=96)

    cmd = _cut(svc, tmp_path)

    assert cmd == [
        resolve_ffmpeg(svc.config),
        "-y",
        "-ss",
        "4.7",
        "-t",
        "2.6",
        "-i",
        str(tmp_path / "ep.mkv"),
        "-map",
        "0:a:0",
        "-vn",
        "-acodec",
        encoder,
        "-b:a",
        "96k",
        "-ac",
        "1",
        str(tmp_path / f"clip.{audio_format}"),
    ]

"""F-02 (R1): Condense runs the clip loudness filter on its output, behind the same switch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.services.audio_condenser import AudioCondenserService
from anki_miner.services.media_extractor import LOUDNORM_FILTER, LOUDNORM_SAMPLE_RATE
from tests.unit.test_audio_condenser import (
    _POPEN,
    _RESOLVE,
    _factory,
    _make_config,
    _make_extractor,
    _progress_block,
)

_GRAPH = "aselect='(between(t,0.000,2.000)+between(t,3.000,5.000))',asetpts=N/SR/TB"


def _service(tmp_path: Path, *, normalize: bool) -> AudioCondenserService:
    config = _make_config(tmp_path)
    config.normalize_audio_loudness = normalize
    return AudioCondenserService(config, _make_extractor(0))


def _condense(svc: AudioCondenserService, tmp_path: Path, suffix: str, **kwargs) -> dict:
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000), (3000, 5000)], tmp_path / f"out{suffix}", **kwargs)
    return captured


def test_switch_on_appends_loudnorm_to_the_graph(tmp_path):
    captured = _condense(_service(tmp_path, normalize=True), tmp_path, ".mp3")

    assert captured["graph"] == f"{_GRAPH},{LOUDNORM_FILTER}"


@pytest.mark.parametrize("suffix", [".mp3", ".opus", ".flac"])
def test_switch_on_pins_the_output_rate(tmp_path, suffix):
    cmd = _condense(_service(tmp_path, normalize=True), tmp_path, suffix)["cmd"]

    assert cmd.count("-ar") == 1
    assert cmd[cmd.index("-ar") + 1] == LOUDNORM_SAMPLE_RATE


def test_a_merge_part_keeps_one_rate_flag(tmp_path):
    cmd = _condense(_service(tmp_path, normalize=True), tmp_path, ".mp3", uniform_layout=True)["cmd"]

    assert cmd.count("-ar") == 1
    assert cmd[cmd.index("-ar") + 1] == "48000"


@pytest.mark.parametrize(
    ("suffix", "encoder_args"),
    [
        (".mp3", ["-c:a", "libmp3lame", "-b:a", "96k"]),
        (".opus", ["-c:a", "libopus", "-b:a", "96k", "-ac", "2"]),
        (".flac", ["-c:a", "flac"]),
    ],
)
def test_switch_off_keeps_the_condense_command_and_graph_byte_identical(tmp_path, suffix, encoder_args):
    captured = _condense(_service(tmp_path, normalize=False), tmp_path, suffix, bitrate_kbps=96)

    assert captured["graph"] == _GRAPH
    assert captured["cmd"][:-1] == [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-i",
        "/v/in.mkv",
        "-map",
        "0:0",
        "-vn",
        "-sn",
        "-dn",
        "-/filter:a",
        captured["graph_path"],
        *encoder_args,
    ]

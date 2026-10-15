"""Condense reads the lines mining reads (F-08, MS-12) and keeps short pauses (MS-10)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pysubs2

from anki_miner.services.audio_condenser import CondenseStatus, condense_one, load_subtitle_events


def _write_ass(path: Path, cues: list[tuple[int, int, str, str]]) -> Path:
    subs = pysubs2.SSAFile()
    for style in sorted({cue[3] for cue in cues}):
        subs.styles[style] = pysubs2.SSAStyle()
    for start, end, text, style in cues:
        subs.append(pysubs2.SSAEvent(start=start, end=end, text=text, style=style))
    subs.save(str(path))
    return path


class _Recorder:
    """The part of AudioCondenserService that condense_one calls: records periods, writes the output."""

    def __init__(self) -> None:
        self.periods: list[list[tuple[int, int]]] = []

    def condense(self, media, periods, out_audio, **_kwargs):
        self.periods.append(list(periods))
        Path(out_audio).write_bytes(b"AUDIO")
        return True, None


def _config(*, skip: bool) -> MagicMock:
    cfg = MagicMock()
    cfg.ffprobe_location = None
    cfg.skip_opening_ending = skip
    return cfg


_EPISODE = [
    (1000, 2000, "Hello", "Default"),
    (3000, 4000, "Lyric", "OP"),
    (5000, 6000, "Bye", "Default"),
    (7000, 8000, "Tell me", "Default"),
]


def test_load_drops_song_lines_only_when_asked(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)

    assert [event[2] for event in load_subtitle_events(sub, skip_non_dialogue=True)] == ["Hello", "Bye", "Tell me"]
    assert [event[2] for event in load_subtitle_events(sub)] == ["Hello", "Lyric", "Bye", "Tell me"]


def test_load_collapses_a_karaoke_burst(tmp_path):
    frames = [(3000 + 40 * i, 3040 + 40 * i, rf"{{\k{i}}}La la", "Karaoke") for i in range(10)]
    sub = _write_ass(tmp_path / "ep01.ass", [(0, 1000, "Hi", "Default"), *frames])

    assert [(start, end) for start, end, _text in load_subtitle_events(sub)] == [(0, 1000), (3000, 3400)]


def test_condense_drops_song_lines_with_the_switch_on(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)
    svc = _Recorder()

    result = condense_one(svc, _config(skip=True), tmp_path / "ep01.mkv", sub, tmp_path / "out.mp3", padding_ms=0)

    assert result.status is CondenseStatus.SUCCESS
    assert svc.periods == [[(1000, 2000), (5000, 6000), (7000, 8000)]]


def test_condense_switch_off_keeps_styled_lines(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)
    svc = _Recorder()

    condense_one(svc, _config(skip=False), tmp_path / "ep01.mkv", sub, tmp_path / "out.mp3", padding_ms=0)

    assert svc.periods == [[(1000, 2000), (3000, 4000), (5000, 6000), (7000, 8000)]]


def test_a_config_mock_without_the_field_set_skips_nothing(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)
    cfg = MagicMock()
    cfg.ffprobe_location = None  # skip_opening_ending stays an auto-attribute, which is not True
    svc = _Recorder()

    condense_one(svc, cfg, tmp_path / "ep01.mkv", sub, tmp_path / "out.mp3", padding_ms=0)

    assert svc.periods == [[(1000, 2000), (3000, 4000), (5000, 6000), (7000, 8000)]]

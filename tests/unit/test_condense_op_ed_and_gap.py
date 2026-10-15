"""Condense reads the lines mining reads (F-08, MS-12) and keeps short pauses (MS-10)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pysubs2

from anki_miner.services.audio_condenser import (
    CONDENSE_JOIN_GAP_MS,
    CondenseStatus,
    build_periods,
    condense_one,
    drop_events_in_ranges,
    load_subtitle_events,
    map_events_to_condensed,
)


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


_PROBE = "anki_miner.services.audio_condenser.op_ed_skip_ranges"
_RESOLVE_FFPROBE = "anki_miner.services.audio_condenser.resolve_ffprobe"


def test_drop_events_in_ranges_uses_the_midpoint():
    events = [(0, 2000, "a"), (58_000, 61_000, "b"), (59_000, 61_000, "c"), (100_000, 101_000, "d")]

    # a: middle 1.0 s, in. b: 59.5 s, in. c: 60.0 s, the open end, out. d: out.
    assert drop_events_in_ranges(events, ((0.0, 60.0),)) == [(59_000, 61_000, "c"), (100_000, 101_000, "d")]
    assert drop_events_in_ranges(events, ()) == events


def _chaptered(tmp_path: Path) -> tuple[Path, Path]:
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_ass(
        tmp_path / "ep01.ass",
        [
            (1000, 2000, "Lyric one", "Default"),
            (5000, 6000, "Lyric two", "Default"),
            (100_000, 101_000, "Hello", "Default"),
        ],
    )
    return media, sub


def test_condense_leaves_out_cues_in_the_medias_op_chapter(tmp_path):
    media, sub = _chaptered(tmp_path)
    svc = _Recorder()

    with patch(_RESOLVE_FFPROBE, lambda config: "ffprobe-x"), patch(_PROBE, return_value=((0.0, 90.0),)) as probe:
        result = condense_one(
            svc, _config(skip=True), media, sub, tmp_path / "out.mp3", padding_ms=0, collect_events=True
        )

    probe.assert_called_once_with(media, "ffprobe-x")
    assert svc.periods == [[(100_000, 101_000)]]
    assert result.condensed_events == ((0, 1000, "Hello"),)


def test_the_offset_puts_cues_on_the_media_timeline(tmp_path):
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_ass(tmp_path / "ep01.ass", [(0, 1000, "Lyric", "Default"), (10_000, 11_000, "Hello", "Default")])
    svc = _Recorder()

    # File 0-1 s is media 2-3 s with a +2 s offset: inside the 1.5-3.5 s chapter.
    with patch(_RESOLVE_FFPROBE, lambda config: "ffprobe"), patch(_PROBE, return_value=((1.5, 3.5),)):
        condense_one(svc, _config(skip=True), media, sub, tmp_path / "out.mp3", offset_ms=2000, padding_ms=0)

    assert svc.periods == [[(12_000, 13_000)]]


def test_condense_switch_off_never_probes(tmp_path):
    media, sub = _chaptered(tmp_path)
    svc = _Recorder()

    with patch(_PROBE, side_effect=AssertionError("probed with the switch off")):
        result = condense_one(svc, _config(skip=False), media, sub, tmp_path / "out.mp3", padding_ms=0)

    assert result.status is CondenseStatus.SUCCESS
    assert svc.periods == [[(1000, 2000), (5000, 6000), (100_000, 101_000)]]


def test_the_join_gap_is_one_second():
    assert CONDENSE_JOIN_GAP_MS == 1000


def test_kept_stretches_closer_than_a_second_join():
    assert build_periods([(0, 1000, "a"), (1900, 3000, "b")], 0) == [(0, 3000)]


def test_a_second_apart_stays_split():
    assert build_periods([(0, 1000, "a"), (2000, 3000, "b")], 0) == [(0, 1000), (2000, 3000)]


def test_the_gap_is_measured_between_padded_stretches():
    # Raw gap 1900 ms; padded by 500 ms on each side the stretches are 900 ms apart.
    assert build_periods([(1000, 2000, "a"), (3900, 5000, "b")], 500) == [(500, 5500)]
    # Raw gap 2000 ms; padded, exactly 1000 ms apart: not closer than a second.
    assert build_periods([(1000, 2000, "a"), (4000, 5000, "b")], 500) == [(500, 2500), (3500, 5500)]


def test_join_gap_zero_is_the_old_rule():
    assert build_periods([(0, 1000, "a"), (1000, 2000, "b")], 0, join_gap_ms=0) == [(0, 2000)]
    assert build_periods([(0, 1000, "a"), (1500, 2000, "b")], 0, join_gap_ms=0) == [(0, 1000), (1500, 2000)]


def test_a_joined_pause_stays_in_the_condensed_timeline():
    events = [(0, 1000, "a"), (1900, 3000, "b")]

    assert map_events_to_condensed(events, build_periods(events, 0)) == [(0, 1000, "a"), (1900, 3000, "b")]


def test_condense_one_sends_the_joined_stretch(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", [(1000, 2000, "Hi", "Default"), (2600, 3000, "there", "Default")])
    svc = _Recorder()

    condense_one(svc, _config(skip=False), tmp_path / "ep01.mkv", sub, tmp_path / "out.mp3", padding_ms=0)

    assert svc.periods == [[(1000, 3000)]]

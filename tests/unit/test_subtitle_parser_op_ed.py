"""The mining parser reads one event pass: songs and signs (F-08) and karaoke bursts (MS-12)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pysubs2

from anki_miner.services.subtitle_parser import PARSE_RELEVANT_CONFIG_FIELDS, SubtitleParserService


def _write_ass(path: Path, cues: list[tuple[int, int, str, str]]) -> Path:
    subs = pysubs2.SSAFile()
    for style in sorted({cue[3] for cue in cues}):
        subs.styles[style] = pysubs2.SSAStyle()
    for start, end, text, style in cues:
        subs.append(pysubs2.SSAEvent(start=start, end=end, text=text, style=style))
    subs.save(str(path))
    return path


_EPISODE = [
    (0, 2000, "猫が好きだ", "Default"),
    (2000, 4000, "犬も好きだ", "Default"),
    (4000, 6000, "空を飛ぶ歌", "OP"),
    (6000, 8000, r"{\pos(320,40)}ラーメン屋", "Sign"),
    (8000, 10000, "また明日", "Default"),
]


def _texts(parser: SubtitleParserService, sub: Path) -> list[str]:
    return [text for _start, _end, text in parser.parse_raw_entries(sub, 0.0)]


def test_switch_on_drops_song_and_sign_lines(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)
    parser = SubtitleParserService(replace(test_config, skip_opening_ending=True))

    assert _texts(parser, sub) == ["猫が好きだ", "犬も好きだ", "また明日"]


def test_switch_off_keeps_styled_lines(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)
    parser = SubtitleParserService(replace(test_config, skip_opening_ending=False))

    assert _texts(parser, sub) == ["猫が好きだ", "犬も好きだ", "空を飛ぶ歌", "ラーメン屋", "また明日"]


def test_switch_on_keeps_song_lines_out_of_mined_sentences(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)
    parser = SubtitleParserService(replace(test_config, skip_opening_ending=True))

    sentences = {word.sentence for word in parser.parse_subtitle_file(sub, 0.0)}

    assert "猫が好きだ" in sentences
    assert "空を飛ぶ歌" not in sentences
    assert "ラーメン屋" not in sentences


def test_a_karaoke_burst_is_one_line_and_counts_once(test_config, tmp_path):
    frames = [(3000 + 40 * i, 3040 + 40 * i, rf"{{\k{i}}}猫が好き", "OP") for i in range(8)]
    sub = _write_ass(tmp_path / "ep01.ass", [(0, 2000, "猫だ", "Default"), *frames])
    parser = SubtitleParserService(replace(test_config, skip_opening_ending=False))

    assert parser.parse_raw_entries(sub, 0.0) == [(0.0, 2.0, "猫だ"), (3.0, 3.32, "猫が好き")]
    assert parser.count_lemmas(sub)["猫"] == 2


def test_an_unstyled_srt_reads_as_before(test_config, tmp_path):
    srt = tmp_path / "ep01.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n猫だ\n\n2\n00:00:02,000 --> 00:00:03,000\n犬だ\n\n", encoding="utf-8"
    )
    on = SubtitleParserService(replace(test_config, skip_opening_ending=True))
    off = SubtitleParserService(replace(test_config, skip_opening_ending=False))

    assert on.parse_raw_entries(srt, 0.0) == off.parse_raw_entries(srt, 0.0) == [(1.0, 2.0, "猫だ"), (2.0, 3.0, "犬だ")]


def test_the_switch_is_a_parse_relevant_field():
    assert "skip_opening_ending" in PARSE_RELEVANT_CONFIG_FIELDS


# Media time = file time + offset. The OP chapter is 0-60 s of the video.
_TIMELINE = [
    (10_000, 12_000, "歌の一行目", "Default"),
    (40_000, 42_000, "歌の二行目", "Default"),
    (100_000, 102_000, "猫が好きだ", "Default"),
    (200_000, 202_000, "また明日", "Default"),
]


def _chapter_parser(test_config) -> SubtitleParserService:
    return SubtitleParserService(replace(test_config, skip_opening_ending=True))


def test_cues_inside_a_registered_chapter_are_left_out(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _TIMELINE)
    parser = _chapter_parser(test_config)

    parser.set_skip_ranges(sub, ((0.0, 60.0),), offset=0.0)

    assert _texts(parser, sub) == ["猫が好きだ", "また明日"]
    assert {word.sentence for word in parser.parse_subtitle_file(sub, 0.0)} <= {"猫が好きだ", "また明日"}


def test_the_run_offset_moves_the_chapter_onto_the_file(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _TIMELINE)
    parser = _chapter_parser(test_config)

    # With a +30 s offset, file 11 s is media 41 s (inside 0-60) and file 41 s is media 71 s (outside).
    parser.set_skip_ranges(sub, ((0.0, 60.0),), offset=30.0)

    assert _texts(parser, sub) == ["歌の二行目", "猫が好きだ", "また明日"]


def test_every_call_offset_reads_the_same_lines(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _TIMELINE)
    parser = _chapter_parser(test_config)
    parser.set_skip_ranges(sub, ((0.0, 60.0),), offset=30.0)

    at_zero = parser.parse_raw_entries(sub, 0.0)
    at_run = parser.parse_raw_entries(sub, 30.0)

    assert [entry[2] for entry in at_zero] == [entry[2] for entry in at_run] == ["歌の二行目", "猫が好きだ", "また明日"]
    assert at_run[0][:2] == (70.0, 72.0)


def test_the_cache_replay_applies_ranges_registered_later(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _TIMELINE)
    parser = _chapter_parser(test_config)
    first = {word.sentence for word in parser.parse_subtitle_file(sub, 0.0)}
    assert "歌の一行目" in first

    parser.set_skip_ranges(sub, ((0.0, 60.0),), offset=0.0)
    second = {word.sentence for word in parser.parse_subtitle_file(sub, 0.0)}

    assert "歌の一行目" not in second
    assert "歌の二行目" not in second


def test_empty_ranges_forget_the_file(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _TIMELINE)
    parser = _chapter_parser(test_config)
    parser.set_skip_ranges(sub, ((0.0, 60.0),), offset=0.0)

    parser.set_skip_ranges(sub, (), offset=0.0)

    assert len(_texts(parser, sub)) == 4


def test_the_line_index_and_the_raw_entries_agree(test_config, tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _TIMELINE)
    parser = _chapter_parser(test_config)
    parser.set_skip_ranges(sub, ((0.0, 60.0),), offset=0.0)

    _words, index = parser.parse_subtitle_file_with_index(sub, 0.0)
    raw = parser.parse_raw_entries(sub, 0.0)

    assert {(line.start_time, line.end_time, line.line_text) for line in index} <= set(raw)
    assert all(line.line_text not in {"歌の一行目", "歌の二行目"} for line in index)

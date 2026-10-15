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

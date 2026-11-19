"""A Chinese line in a Japanese subtitle file is listed but never mined."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import _create_subtitle_parser
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

_CREDIT = "翻译 校对 禁止用于商业用途"
_JAPANESE = "商業利用は禁止です"


def _srt(tmp_path, *lines):
    path = tmp_path / "sub.srt"
    cues = "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{line}\n\n" for i, line in enumerate(lines, 1))
    path.write_text(cues, encoding="utf-8")
    return path


@pytest.mark.parametrize("build", [_create_subtitle_parser, get_profile("ja").create_parser])
def test_japanese_parser_mines_nothing_from_a_chinese_line(tmp_path, build):
    # Every kanji is Japanese script to the gate, so a fansub credit line was
    # mined: 禁止 and 用途 cards whose sentence is Chinese.
    parser = build(AnkiMinerConfig())
    path = _srt(tmp_path, _CREDIT, _JAPANESE)

    words = parser.parse_subtitle_file(path)

    assert {word.sentence for word in words} == {_JAPANESE}
    assert "禁止" in {word.mined_form for word in words}


def test_a_chinese_line_is_still_listed(tmp_path):
    # Previews and the line list show the file as written; only mining skips.
    parser = _create_subtitle_parser(AnkiMinerConfig())

    assert [text for _start, _end, text in parser.parse_raw_entries(_srt(tmp_path, _CREDIT, _JAPANESE))] == [
        _CREDIT,
        _JAPANESE,
    ]


def test_korean_parser_has_no_chinese_line_skip():
    # Korean runs the same text cleaning as Japanese; the skip is the Japanese factory's alone.
    parser = _create_subtitle_parser(switch_language(AnkiMinerConfig(), "ko"), defer_tagger=True)

    assert parser._skip_line is None


@pytest.mark.parametrize(
    "chinese",
    [
        "你真的没事吗？",  # simplified
        "你真的沒事嗎？",  # traditional: only the kana-less line itself shows it is Chinese
    ],
)
def test_a_bilingual_cue_keeps_its_japanese_line(tmp_path, chinese):
    # Judged as one flattened cue, the simplified pair lost its Japanese half
    # and the traditional pair kept the Chinese in the card sentence.
    parser = _create_subtitle_parser(AnkiMinerConfig())
    path = _srt(tmp_path, f"本当に大丈夫ですか？\n{chinese}")

    words = parser.parse_subtitle_file(path)

    assert {word.sentence for word in words} == {"本当に大丈夫ですか？"}
    assert "大丈夫" in {word.mined_form for word in words}


@pytest.mark.parametrize("build", [_create_subtitle_parser, get_profile("ja").create_parser])
def test_a_one_role_credit_cue_is_judged_before_its_label_is_stripped(tmp_path, build):
    # The simplified role label is the line's only Chinese; the speaker-tag strip took it.
    parser = build(AnkiMinerConfig())
    path = _srt(tmp_path, "翻译：小明", "校对：路人甲", "时间轴：天空", "猫が好きです", "翻译：小明\n明日行きます")

    words = parser.parse_subtitle_file(path)

    assert {word.sentence for word in words} == {"猫が好きです", "明日行きます"}
    assert "猫" in {word.mined_form for word in words}
    assert [text for _start, _end, text in parser.parse_raw_entries(path)][:3] == [
        "翻译：小明",
        "校对：路人甲",
        "时间轴：天空",
    ]

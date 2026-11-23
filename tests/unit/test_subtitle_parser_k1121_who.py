"""A person's name is no card (session K1121, wave 3, lane WHO)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

# What the dictionary lists: words, and no person's name.
_HEADWORDS = (
    "森",
    "悪魔",
    "東京",
    "京都",
    "昭和",
    "火星",
    "昨日",
    "休み",
    "元気",
    "歌",
    "中",
    "村",
    "来る",
    "行く",
    "会う",
    "聞く",
    "歩く",
    "早い",
    "寒い",
    "ふん",
    "よし",
    "知る",
)
_COMMON = frozenset(_HEADWORDS)
# Proper nouns allowed, as the measuring settings have them.
_NAMES_MINED = dataclasses.replace(AnkiMinerConfig(), excluded_subtypes=("非自立", "接尾", "助動詞", "接頭"))


def _service(
    config: AnkiMinerConfig | None = None, *, whitelist: frozenset[str] | None = None
) -> SubtitleParserService:
    spelled = set(_HEADWORDS)
    return SubtitleParserService(
        config or _NAMES_MINED,
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
        term_common_lookup=lambda terms: {term: term in _COMMON for term in terms},
        name_piece_rejects=True,
        force_include=None if whitelist is None else whitelist.__contains__,
    )


def _names_mined(service: SubtitleParserService) -> SubtitleParserService:
    """The same parser with the switch on: a person's name is a card again."""
    service._mine_person_names = True
    return service


def _srt(path: Path, lines: list[str]) -> Path:
    path.write_text(
        "".join(
            f"{index + 1}\n00:00:{2 * index:02d},000 --> 00:00:{2 * index + 1:02d},000\n{line}\n\n"
            for index, line in enumerate(lines)
        ),
        encoding="utf-8",
    )
    return path


def _fronts(service: SubtitleParserService, tmp_path: Path, line: str) -> list[str]:
    """The card fronts of one cue, with nothing dropped as a repeat."""
    path = _srt(tmp_path / "line.srt", [line])
    (state,) = service._iter_parsed_lines(path)
    words, _index = service._emit_line_words_and_index(state, None, collect_index=False, rejects={})
    return [word.mined_form for word in words]


class TestTaggedPersonName:
    """W-1: a token the tagger tags 固有名詞-人名 is no card, whatever the subtype settings."""

    @pytest.mark.parametrize(
        ("line", "name", "rest"),
        [
            ("佐藤さんが来た", "佐藤", ["来る"]),
            ("美咲ちゃんは元気だ", "美咲", ["元気"]),
            ("タケシ！ 早く来い", "タケシ", ["早い", "来る"]),
            ("昨日は山田は休みだった", "山田", ["昨日", "休み"]),
            ("健太は東京へ行った", "健太", ["東京", "行く"]),
        ],
    )
    def test_a_tagged_name_is_no_card(self, tmp_path: Path, line: str, name: str, rest: list[str]) -> None:
        assert _fronts(_service(), tmp_path, line) == rest
        assert sorted(_fronts(_names_mined(_service()), tmp_path, line)) == sorted([name, *rest])

    @pytest.mark.parametrize(
        ("line", "fronts"),
        [
            ("東京に行く", ["東京", "行く"]),
            ("京都は寒い", ["京都", "寒い"]),
            ("昭和の歌を聞く", ["昭和", "歌", "聞く"]),
            ("火星に行きたい", ["火星", "行く"]),
            ("森の中を歩いた", ["森", "中", "歩く"]),
        ],
    )
    def test_a_place_an_era_and_a_noun_stay(self, tmp_path: Path, line: str, fronts: list[str]) -> None:
        assert _fronts(_service(), tmp_path, line) == fronts

    def test_a_common_word_the_tagger_calls_a_name_stays(self, tmp_path: Path) -> None:
        # The tagger reads 悪魔 before が as a person's name; the dictionary lists it as a common word.
        assert _fronts(_service(), tmp_path, "悪魔が村に来た") == ["悪魔", "村", "来る"]

    def test_a_common_word_before_an_honorific_is_the_name(self, tmp_path: Path) -> None:
        assert _fronts(_service(), tmp_path, "森さんに会った") == ["会う"]
        assert _fronts(_names_mined(_service()), tmp_path, "森さんに会った") == ["森", "会う"]

    def test_a_whitelisted_name_is_mined(self, tmp_path: Path) -> None:
        service = _service(whitelist=frozenset({"佐藤"}))
        assert _fronts(service, tmp_path, "佐藤さんが来た") == ["佐藤", "来る"]
        assert _fronts(service, tmp_path, "昨日は山田は休みだった") == ["昨日", "休み"]

    def test_a_refused_name_is_no_preference_reject(self, tmp_path: Path) -> None:
        service = _service()
        (state,) = service._iter_parsed_lines(_srt(tmp_path / "line.srt", ["佐藤さんが来た"]))
        rejects: dict = {}
        service._emit_line_words_and_index(state, None, collect_index=False, rejects=rejects)
        assert "佐藤" not in rejects

    def test_text_units_get_the_rule(self) -> None:
        units = [ReadingUnit(index=0, text="佐藤さんが来た", location_label="p.1")]
        words, _index, counts = _service().parse_text_units(units, want_line_index=False)
        assert [word.mined_form for word in words] == ["来る"]
        assert "佐藤" not in counts
        words, _index, counts = _names_mined(_service()).parse_text_units(units, want_line_index=False)
        assert [word.mined_form for word in words] == ["佐藤", "来る"]

    def test_the_sentence_reading_does_not_change(self, tmp_path: Path) -> None:
        path = _srt(tmp_path / "line.srt", ["佐藤さんが来た"])
        off = {word.mined_form: word.sentence_furigana for word in _service().parse_subtitle_file(path)}
        on = {word.mined_form: word.sentence_furigana for word in _names_mined(_service()).parse_subtitle_file(path)}
        assert off["来る"] == on["来る"]
        assert "佐藤[" in off["来る"]

    def test_the_switch_changes_nothing_where_proper_nouns_are_excluded(self, tmp_path: Path) -> None:
        for line in ("佐藤さんが来た", "昨日は山田は休みだった", "東京に行く"):
            off = _fronts(_service(AnkiMinerConfig()), tmp_path, line)
            assert off == _fronts(_names_mined(_service(AnkiMinerConfig())), tmp_path, line)
            assert "佐藤" not in off and "山田" not in off

    @pytest.mark.parametrize("line", ["フン… 知らないよ", "ヨシ！ 行くぞ"])
    def test_a_common_kana_word_written_in_katakana_stays(self, tmp_path: Path, line: str) -> None:
        # The tagger reads フン before … and ヨシ before ！ as names; ふん and よし are common words.
        fronts = _fronts(_service(), tmp_path, line)
        assert fronts == _fronts(_names_mined(_service()), tmp_path, line)
        assert fronts[0] == line[:2]

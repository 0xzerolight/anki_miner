"""Tests for number + counter cards (Settings → Word Filters → Mine numbers with counters)."""

from dataclasses import replace

import pytest

from anki_miner.models.reading import ReadingUnit
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.number_counters import counter_of, counter_reading, parse_number
from anki_miner.services.subtitle_parser import PARSE_RELEVANT_CONFIG_FIELDS, SubtitleParserService


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("3", 3),
        ("１０", 10),
        ("二十", 20),
        ("三百五十", 350),
        ("一万二千", 12000),
        ("二〇二四", 2024),
        ("10万", 100_000),
    ],
)
def test_parses_digit_kanji_and_mixed_numerals(text, value):
    assert parse_number(text) == value


@pytest.mark.parametrize("text", ["", "三日月", "1.5", "百a"])
def test_rejects_what_is_not_a_numeral(text):
    assert parse_number(text) is None


@pytest.mark.parametrize(
    ("number", "counter", "reading"),
    [
        # Gemination and the h-row's p/b (本, 分, 匹, 杯).
        (1, "本", "いっぽん"),
        (3, "本", "さんぼん"),
        (4, "本", "よんほん"),
        (6, "本", "ろっぽん"),
        (20, "本", "にじゅっぽん"),
        (300, "本", "さんびゃっぽん"),
        (1000, "本", "せんぼん"),
        (3, "分", "さんぷん"),
        (4, "分", "よんぷん"),
        (5, "分", "ごふん"),
        (3, "匹", "さんびき"),
        (3, "杯", "さんばい"),
        # k-row: gemination, and 階's voiced さんがい.
        (1, "個", "いっこ"),
        (8, "個", "はっこ"),
        (6, "回", "ろっかい"),
        (3, "階", "さんがい"),
        (1, "か月", "いっかげつ"),
        # s-row and the whole-number readings.
        (1, "歳", "いっさい"),
        (20, "歳", "はたち"),
        (100, "歳", "ひゃくさい"),
        (1, "人", "ひとり"),
        (2, "人", "ふたり"),
        (4, "人", "よにん"),
        (14, "人", "じゅうよにん"),
        (4, "時", "よじ"),
        (7, "時", "しちじ"),
        (9, "時", "くじ"),
        (4, "月", "しがつ"),
        (3, "日", "みっか"),
        (20, "日", "はつか"),
        (17, "日", "じゅうしちにち"),
        (4, "年", "よねん"),
        (4, "円", "よえん"),
        (10_000, "円", "いちまんえん"),
        (3, "つ", "みっつ"),
    ],
)
def test_reads_the_counter_with_its_sound_changes(number, counter, reading):
    assert counter_reading(number, counter) == reading


@pytest.mark.parametrize(("number", "counter"), [(13, "月"), (11, "つ"), (0, "人"), (3, "枚目"), (3, "足")])
def test_no_reading_outside_the_table(number, counter):
    assert counter_reading(number, counter) is None


def test_names_the_counter_of_a_front_for_its_definition():
    assert counter_of("4人") == "人"
    assert counter_of("３ヶ月") == "ヶ月"
    assert counter_of("人") is None
    assert counter_of("先日") is None


def test_the_lookup_ladder_tries_the_counter_first():
    assert DefinitionService._fallback_candidates("4人", "", None)[0] == ("人", 0)


def test_the_setting_is_parse_relevant():
    # It changes the cached line state, so a reused parser must match it.
    assert "mine_number_counters" in PARSE_RELEVANT_CONFIG_FIELDS


def _mine(test_config, on, *lines):
    config = replace(test_config, mine_number_counters=on)
    service = SubtitleParserService(config, kana_attest_lookup=lambda forms: dict.fromkeys(forms, True))
    units = [ReadingUnit(text=line, index=i, location_label="t") for i, line in enumerate(lines)]
    words, _index, _counts = service.parse_text_units(units, want_line_index=False)
    return {word.mined_form: word.expression_reading for word in words}


class TestMining:
    def test_mines_a_number_and_its_counter_with_the_setting_on(self, test_config):
        fronts = _mine(test_config, True, "4人来た", "３本ある", "１つずつ")
        assert fronts["4人"] == "よにん"
        assert fronts["３本"] == "さんぼん"
        assert fronts["１つ"] == "ひとつ"

    def test_leaves_numbers_alone_with_the_setting_off(self, test_config):
        fronts = _mine(test_config, False, "4人来た", "3年経った")
        assert "4人" not in fronts and "3年" not in fronts
        assert "年" in fronts  # today's behaviour: the counter alone

    def test_joins_a_numeral_split_over_tokens(self, test_config):
        assert _mine(test_config, True, "三百五十円")["三百五十円"] == "さんびゃくごじゅうえん"

    def test_leaves_a_fraction(self, test_config):
        assert not any(front.startswith("3分") for front in _mine(test_config, True, "3分の1"))

    def test_never_merges_across_a_space(self, test_config):
        assert "一人" not in _mine(test_config, True, "一 人")

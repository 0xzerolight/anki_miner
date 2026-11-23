"""Settings → Word Filters → Mine grammar points: sentence grammar (んだ, ください) as cards."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services import kana_words, lexical_joiner
from anki_miner.services.subtitle_parser import PARSE_RELEVANT_CONFIG_FIELDS, SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

#: One line per grammar point, and the card it makes with the setting on.
_GRAMMAR_LINES = (
    ("明日行くんだよ", "んだ"),
    ("高いんですか", "んです"),
    ("魚を食べたいのです", "のです"),
    ("帰るのだ", "のだ"),
    ("学生ですか", "ですか"),
    ("そうだろう", "だろう"),
    ("雨が降るでしょう", "でしょう"),
    ("嘘だろ", "だろ"),
    ("雨でしょ", "でしょ"),
    ("やめてください", "ください"),
    ("嘘じゃない", "じゃない"),
    ("それは本ではない", "ではない"),
    ("っていうか誰だよ", "っていうか"),
    ("本当に好きなんだ", "なんだ"),
    ("彼は歌手なんですか", "なんです"),
    ("早く行かなきゃいけない", "いけない"),
    ("遅れてはいけません", "いけません"),
    ("食べなきゃいかん", "いかん"),
    ("宿題をしなければならない", "ならない"),
    ("行かなきゃならん", "ならん"),
    ("書かなければなりません", "なりません"),
    ("猫みたいだ", "みたい"),
    ("いいじゃないか", "じゃないか"),
    ("そうでしょうか", "でしょうか"),
    ("明日は雪らしい", "らしい"),
    ("素人ごときが言うな", "ごとき"),
    ("矢のごとく飛ぶ", "ごとく"),
    ("私如きには無理だ", "如き"),
    ("光の如く速い", "如く"),
    ("今日は寝るべきだ", "べき"),
    ("検討すべき問題だ", "すべき"),
    ("もう帰ったやろ", "やろ"),
    ("それは嘘やない", "やない"),
    ("これ本物じゃね？", "じゃね"),
    ("誰にでも起こりうる", "うる"),
    ("あんな所へ二度と行くものか", "ものか"),
)

#: Joined expressions and connectives that are no grammar points: always mined.
_ALWAYS_ON = (
    ("猫という動物", "という"),
    ("先生として働く", "として"),
    ("行ったことがある", "ことがある"),
    ("雨かもしれない", "かもしれない"),
    ("だったら帰る", "だったら"),
    ("そしたら動かない", "そしたら"),
    ("父親のくせに泣く", "くせに"),
    ("猫っていう動物", "っていう"),
)


def _words(on: bool, text: str):
    service = SubtitleParserService(
        replace(AnkiMinerConfig(), mine_grammar_points=on),
        term_lookup=lambda terms: set(),
        kana_attest_lookup=lambda forms: dict.fromkeys(forms, True),
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return words


def _fronts(on: bool, text: str) -> list[str]:
    return [word.mined_form for word in _words(on, text)]


def test_the_setting_is_on_by_default():
    assert AnkiMinerConfig().mine_grammar_points is True


def test_the_setting_is_parse_relevant():
    # It changes the cached line state, so a reused parser must match it.
    assert "mine_grammar_points" in PARSE_RELEVANT_CONFIG_FIELDS


class TestMining:
    @pytest.mark.parametrize(("text", "front"), _GRAMMAR_LINES)
    def test_mines_the_grammar_point_with_the_setting_on(self, text, front):
        assert front in _fronts(True, text)

    @pytest.mark.parametrize(("text", "front"), _GRAMMAR_LINES)
    def test_leaves_it_out_with_the_setting_off(self, text, front):
        assert front not in _fronts(False, text)

    @pytest.mark.parametrize("on", [True, False])
    @pytest.mark.parametrize(("text", "front"), _ALWAYS_ON)
    def test_other_expressions_are_mined_either_way(self, text, front, on):
        assert front in _fronts(on, text)

    def test_off_the_tokens_fall_to_the_shorter_row(self):
        # っていうか is the grammar point; っていう behind it is not.
        assert "っていう" in _fronts(False, "猫っていうか犬だ")

    def test_off_mines_as_a_list_without_the_grammar_rows(self, monkeypatch):
        lines = [text for text, _front in _GRAMMAR_LINES] + [
            "猫っていうか犬だ",
            "まだ読んではないよ",
            "もういいの だから寝る",
            "学生じゃなくて先生だ",
            "何をやってんだよ",
        ]

        def spans(on: bool) -> list[list[tuple[str, int, int]]]:
            return [
                sorted((word.mined_form, word.surface_start, word.highlight_end) for word in _words(on, text))
                for text in lines
            ]

        off = spans(False)
        listed = kana_words.kana_words()
        without = listed._replace(
            joins={key: row for key, row in listed.joins.items() if not row.grammar},
            base_joins={key: row for key, row in listed.base_joins.items() if not row.grammar},
            joined=frozenset(
                row.headword for row in (*listed.joins.values(), *listed.base_joins.values()) if not row.grammar
            ),
        )
        monkeypatch.setattr(kana_words, "kana_words", lambda: without)
        monkeypatch.setattr(lexical_joiner, "kana_words", lambda: without)
        assert spans(True) == off


def test_the_list_marks_exactly_the_grammar_points():
    listed = kana_words.kana_words()
    marked = {row.headword for row in (*listed.joins.values(), *listed.base_joins.values()) if row.grammar}
    assert marked == {front for _text, front in _GRAMMAR_LINES}

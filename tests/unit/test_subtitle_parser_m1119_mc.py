"""Idioms the line spells for the ear or says without their が, joined to the dictionary's headword."""

from __future__ import annotations

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

_IDIOMS = (
    "我を忘れる",
    "手を出す",
    "頭が痛い",
    "機嫌が悪い",
    "腹が減る",
    "貴方達",
    "仲いい",
    "仲がいい",
    "金を貸す",
    "事がない",
)


def _words(text: str, attested=_IDIOMS):
    wanted = set(attested)
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: {term for term in terms if term in wanted},
        kana_attest_lookup=lambda forms: {form: form in wanted for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return words


def _fronts(text: str) -> list[str]:
    return [word.mined_form for word in _words(text)]


class TestSpokenIdioms:
    @pytest.mark.parametrize(
        ("text", "front", "pieces"),
        [
            ("ワレを忘れるな", "我を忘れる", {"我", "ワレ", "忘れる"}),  # a native word in katakana
            ("手ぇを出すな", "手を出す", {"手", "出す"}),  # a drawn-out vowel
            ("頭痛いから帰る", "頭が痛い", {"頭", "痛い"}),  # が dropped before an adjective
            ("機嫌悪かったね", "機嫌が悪い", {"機嫌", "悪い"}),
        ],
    )
    def test_mines_the_dictionary_headword(self, text, front, pieces):
        fronts = _fronts(text)
        assert front in fronts
        assert not pieces & set(fronts)

    def test_the_highlight_follows_the_inflection(self):
        text = "機嫌悪かったね"
        (word,) = [word for word in _words(text) if word.mined_form == "機嫌が悪い"]
        assert text[word.surface_start : word.highlight_end] == "機嫌悪かった"

    def test_a_headword_spelled_as_the_line_wins(self):
        fronts = _fronts("仲いいね")
        assert "仲いい" in fronts
        assert "仲がいい" not in fronts

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("雨降ったな", "雨が降る"),  # a bare noun + verb stays two words (腹減った is a listed idiom now)
            ("そんな事ないよ", "事がない"),  # a formal noun: そんな事ない is not 見た事がない
            ("アンタ達は帰れ", "貴方達"),  # アンタ does not read as its lemma 貴方 (あなた)
            ("かねを貸すな", "金を貸す"),  # the tagger's kanji for a hiragana word is a guess
        ],
    )
    def test_the_parts_stay_apart(self, text, front):
        assert front not in _fronts(text)


_GRAMMAR = ("んだ", "んです", "のです", "のだ", "ですか", "だろう", "でしょう", "ください", "じゃない", "ではない")


def _grammar_words(text: str):
    return _words(text, _GRAMMAR)


class TestSentenceGrammar:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("明日行くんだよ", "んだ"),
            ("高いんですか", "んです"),
            ("魚を食べたいのです", "のです"),
            ("学生ですか", "ですか"),
            ("そうだろう", "だろう"),
            ("魚を食べるでしょう", "でしょう"),
            ("やめてください", "ください"),
            ("学生じゃなくて先生だ", "じゃない"),
            ("学生ではない", "ではない"),
        ],
    )
    def test_mines_the_expression(self, text, front):
        assert front in [word.mined_form for word in _grammar_words(text)]

    @pytest.mark.parametrize(
        ("text", "front", "shorter"),
        [("本当に好きなんだ", "なんだ", "んだ"), ("彼は歌手なんですか", "なんです", "ですか")],
    )
    def test_the_adnominal_na_belongs_to_the_expression(self, text, front, shorter):
        fronts = [word.mined_form for word in _words(text, (*_GRAMMAR, "なんだ", "なんです"))]
        assert front in fronts
        assert shorter not in fronts

    def test_the_longer_expression_takes_the_copula(self):
        fronts = [word.mined_form for word in _grammar_words("高いんですか")]
        assert "ですか" not in fronts

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("本当に好きなんだ", "んだ"),  # after the adnominal な
            ("何をやってんだよ", "んだ"),  # the ん is やっている's own
            ("これは俺のだ", "のだ"),  # the possessive の
            ("魚が死んだ", "んだ"),  # 死ん|だ is the verb
            ("まだ読んではないよ", "ではない"),  # the で is 読む's te-form
            ("死んではない", "ではない"),
            ("まだ読んじゃないよ", "じゃない"),  # the じゃ is 読む's contracted では
        ],
    )
    def test_the_same_kana_elsewhere_is_no_expression(self, text, front):
        assert front not in [word.mined_form for word in _grammar_words(text)]

    @pytest.mark.parametrize(("text", "front"), [("それは本ではない", "ではない"), ("嘘じゃない", "じゃない")])
    def test_the_copula_expression_follows_a_noun(self, text, front):
        assert front in [word.mined_form for word in _grammar_words(text)]

    @pytest.mark.parametrize(
        "text",
        [
            "やめてください",
            "魚を食べたんだ",
            "行かないんです",
            "何をやってんだよ",
            "まだ読んではないよ",
            "まだ読んじゃないよ",
        ],
    )
    def test_no_card_starts_inside_the_word_before_it(self, text):
        words = sorted(_grammar_words(text), key=lambda word: word.surface_start)
        for before, after in zip(words, words[1:], strict=False):
            assert before.highlight_end <= after.surface_start

    def test_a_refused_row_leaves_its_tokens_to_the_next_one(self):
        # の|だ across a space is no のだ; the だ|から behind it opens a clause and still joins.
        fronts = [word.mined_form for word in _words("もういいの だから寝る", (*_GRAMMAR, "だから"))]
        assert "だから" in fronts
        assert "のだ" not in fronts


_CONNECTIVES = (
    "だったら",
    "だが",
    "ですが",
    "ですから",
    "それより",
    "そしたら",
    "としたら",
    "そうやって",
    "くせに",
    "っていう",
)


class TestConnectives:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("だったら帰る", "だったら"),
            ("だが断る", "だが"),
            ("ですから無理です", "ですから"),
            ("それより仕事だ", "それより"),
            ("そしたら動かない", "そしたら"),
            ("そうやって逃げる", "そうやって"),
            ("父親のくせに泣く", "くせに"),
            ("猫っていう動物", "っていう"),
        ],
    )
    def test_mines_the_connective(self, text, front):
        assert front in [word.mined_form for word in _words(text, _CONNECTIVES)]

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("体が丈夫だったら行く", "だったら"),  # the copula's own conditional
            ("静かだが暗い", "だが"),
            ("彼が行くっていうから待つ", "っていう"),  # says, not called
        ],
    )
    def test_the_same_tokens_inside_a_clause_stay_apart(self, text, front):
        assert front not in [word.mined_form for word in _words(text, _CONNECTIVES)]

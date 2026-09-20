"""Word-level pinyin readings with tone marks (spec 9.1: never sandhi-adjusted)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.reading import (
    _ER_IS_A_SYLLABLE,
    ZhReadingSupport,
    pinyin_syllables,
    syllable_tone,
    word_pinyin,
)
from anki_miner.languages.zh.render import ZhToneColorHook


class TestWordPinyin:
    def test_syllables_are_space_separated_with_tone_marks(self) -> None:
        assert word_pinyin("中国") == "zhōng guó"

    @pytest.mark.parametrize(("word", "expected"), [("重要", "zhòng yào"), ("重复", "chóng fù")])
    def test_polyphones_resolve_from_the_phrase_dictionary(self, word: str, expected: str) -> None:
        # The whole jieba segment is handed to pypinyin, never one char at a
        # time — that phrase lookup is the only thing that separates 重 chóng
        # from 重 zhòng.
        assert word_pinyin(word) == expected

    def test_non_hanzi_yields_an_empty_reading(self) -> None:
        assert word_pinyin("ok!") == ""

    @pytest.mark.parametrize(
        ("word", "expected"), [("銀行", "yín háng"), ("重複", "chóng fù"), ("音樂", "yīn yuè"), ("會計", "kuài jì")]
    )
    def test_traditional_polyphones_read_like_their_simplified_twin(self, word: str, expected: str) -> None:
        # pypinyin's phrase dictionary is simplified-only; a traditional word
        # would otherwise be read one character at a time (銀行 yín xíng).
        pytest.importorskip("opencc")
        assert word_pinyin(word) == expected


class TestErhua:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("这儿", "zhèr"),
            ("哪儿", "nǎr"),
            ("那儿", "nàr"),
            ("玩儿", "wánr"),
            ("会儿", "huìr"),
            ("一点儿", "yī diǎnr"),
            ("一会儿", "yī huìr"),
            ("有点儿", "yǒu diǎnr"),
        ],
    )
    def test_a_final_er_colours_the_previous_syllable(self, word: str, expected: str) -> None:
        # pypinyin has no erhua rule, so it returns 儿 as a syllable of its own
        # (zhè ér) — a pronunciation that does not exist.
        assert word_pinyin(word) == expected

    @pytest.mark.parametrize(("word", "expected"), [("女儿", "nǚ ér"), ("婴儿", "yīng ér"), ("新生儿", "xīn shēng ér")])
    def test_er_as_the_noun_head_keeps_its_own_syllable(self, word: str, expected: str) -> None:
        assert word_pinyin(word) == expected

    @pytest.mark.parametrize("word", sorted(_ER_IS_A_SYLLABLE))
    def test_every_excepted_word_really_ends_in_a_full_er(self, word: str) -> None:
        # A member pypinyin reads some other way would be an exception that
        # excepts nothing.
        assert word_pinyin(word).endswith(" ér")

    @pytest.mark.parametrize(("word", "expected"), [("儿子", "ér zi"), ("儿童", "ér tóng")])
    def test_a_non_final_er_is_untouched(self, word: str, expected: str) -> None:
        assert word_pinyin(word) == expected

    def test_a_mixed_token_merges_on_the_last_hanzi(self) -> None:
        # The Latin letter yields no syllable, so the merge has to align on the
        # hanzi, not on the character before 儿.
        assert word_pinyin("T恤儿") == "xùr"

    def test_a_traditional_word_erhuas_like_its_simplified_twin(self) -> None:
        pytest.importorskip("opencc")
        assert word_pinyin("這兒") == "zhèr"


class TestCitationTones:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("一个", "yī gè"),
            ("一起", "yī qǐ"),
            ("一定", "yī dìng"),
            ("一般", "yī bān"),
            ("一样", "yī yàng"),
            ("一次", "yī cì"),
            ("一点", "yī diǎn"),
            ("不是", "bù shì"),
            ("不要", "bù yào"),
            ("不错", "bù cuò"),
        ],
    )
    def test_yi_and_bu_keep_their_citation_tone(self, word: str, expected: str) -> None:
        # pypinyin's phrase dictionary bakes the spoken sandhi into some rows
        # (一个 yí gè) and not others (一样 yī yàng), so cards disagreed with
        # each other and with CC-CEDICT.
        assert word_pinyin(word) == expected

    def test_a_lexical_neutral_bu_is_left_alone(self) -> None:
        # 差不多 chàbuduō is CC-CEDICT's own reading, not sandhi.
        assert word_pinyin("差不多") == "chà bu duō"

    def test_a_traditional_word_gets_the_citation_tone_too(self) -> None:
        pytest.importorskip("opencc")
        assert word_pinyin("一個") == "yī gè"


class TestSyllableTone:
    @pytest.mark.parametrize(
        ("syllable", "tone"), [("zhōng", 1), ("guó", 2), ("nǐ", 3), ("yào", 4), ("le", 5), ("", 5)]
    )
    def test_tone_comes_from_the_diacritic(self, syllable: str, tone: int) -> None:
        assert syllable_tone(syllable) == tone

    def test_an_erhua_syllable_carries_the_tone_of_its_vowel(self) -> None:
        assert syllable_tone("zhèr") == 4


class TestPinyinSyllables:
    def test_pairs_each_syllable_with_its_tone(self) -> None:
        assert pinyin_syllables("中国") == [("zhōng", 1), ("guó", 2)]

    def test_a_traditional_word_gets_its_simplified_twin_tones(self) -> None:
        pytest.importorskip("opencc")
        assert pinyin_syllables("銀行") == [("yín", 2), ("háng", 2)]

    def test_an_erhua_word_yields_one_pair_per_real_syllable(self) -> None:
        assert pinyin_syllables("这儿") == [("zhèr", 4)]


class TestToneColourOnCorrectedReadings:
    """The colour hook paints one span per syllable ``pinyin_syllables`` returns."""

    def _render(self, form: str) -> str:
        config = dataclasses.replace(AnkiMinerConfig(), reading_tone_color=True)
        word = SimpleNamespace(mined_form=form, definition_html="")
        return ZhToneColorHook().render(word, config=config)["expression_pinyin"]

    def test_erhua_is_one_span_not_two(self) -> None:
        # The phantom 儿 used to get an orange tone-2 span of its own.
        assert self._render("这儿") == '<span style="color:#1f6fe0">zhèr</span>'

    def test_a_citation_tone_moves_the_span_colour(self) -> None:
        assert self._render("一个").startswith('<span style="color:#e02020">yī</span>')


class TestZhReadingSupport:
    def test_word_reading_reads_the_token_surface(self) -> None:
        token = LanguageToken(surface="电影", pos1="n", lemma="电影")
        assert ZhReadingSupport().word_reading(token) == "diàn yǐng"

"""Word-level pinyin readings with tone marks (spec 9.1: never sandhi-adjusted)."""

from __future__ import annotations

import pytest

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.reading import ZhReadingSupport, pinyin_syllables, syllable_tone, word_pinyin


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


class TestSyllableTone:
    @pytest.mark.parametrize(
        ("syllable", "tone"), [("zhōng", 1), ("guó", 2), ("nǐ", 3), ("yào", 4), ("le", 5), ("", 5)]
    )
    def test_tone_comes_from_the_diacritic(self, syllable: str, tone: int) -> None:
        assert syllable_tone(syllable) == tone


class TestPinyinSyllables:
    def test_pairs_each_syllable_with_its_tone(self) -> None:
        assert pinyin_syllables("中国") == [("zhōng", 1), ("guó", 2)]


class TestZhReadingSupport:
    def test_word_reading_reads_the_token_surface(self) -> None:
        token = LanguageToken(surface="电影", pos1="n", lemma="电影")
        assert ZhReadingSupport().word_reading(token) == "diàn yǐng"

"""Unit tests for applying a book's furigana to parsed words (SK-31)."""

from __future__ import annotations

from anki_miner.models import TokenizedWord
from anki_miner.services.reading.ruby_hints import apply_author_ruby
from anki_miner.utils.text_utils import hiragana_to_katakana


def _noun(surface: str, kana: str) -> TokenizedWord:
    return TokenizedWord(
        surface=surface,
        lemma=surface,
        reading=hiragana_to_katakana(kana),
        sentence=f"{surface}は良い。",
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        orth_base=surface,
        expression_furigana=f"{surface}[{kana}]",
        expression_reading=kana,
        lemma_reading=kana,
        pos="名詞",
    )


def test_a_noun_takes_the_author_reading_in_all_four_fields() -> None:
    word = _noun("今日", "きょう")

    changed = apply_author_ruby([word], [("今日", "こんにち")])

    assert changed == ["今日"]
    assert (word.expression_reading, word.expression_furigana, word.reading, word.lemma_reading) == (
        "こんにち",
        "今日[こんにち]",
        "コンニチ",
        "こんにち",
    )


def test_a_katakana_rt_is_folded_to_hiragana() -> None:
    word = _noun("朔夜", "さくよる")

    apply_author_ruby([word], [("朔夜", "サクヤ")])

    assert word.expression_reading == "さくや"


def test_a_verb_mined_as_its_dictionary_form_is_left_alone() -> None:
    word = TokenizedWord(
        surface="行っ",
        lemma="行く",
        reading="イッ",
        sentence="行った。",
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        orth_base="行く",
        expression_reading="いく",
        pos="動詞",
    )

    assert apply_author_ruby([word], [("行っ", "おこなっ")]) == []
    assert word.expression_reading == "いく"


def test_a_reading_that_is_not_kana_is_ignored() -> None:
    word = _noun("北京", "ぺきん")

    assert apply_author_ruby([word], [("北京", "Běijīng")]) == []
    assert word.expression_reading == "ぺきん"


def test_two_readings_for_one_base_in_a_unit_are_both_dropped() -> None:
    word = _noun("日", "ひ")

    assert apply_author_ruby([word], [("日", "にち"), ("日", "か")]) == []
    assert word.expression_reading == "ひ"


def test_a_hint_that_matches_the_parsed_reading_changes_nothing() -> None:
    word = _noun("天気", "てんき")

    assert apply_author_ruby([word], [("天気", "てんき")]) == []

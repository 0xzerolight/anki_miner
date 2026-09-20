"""The Vietnamese script gate (S15 heuristic), SDH regex default (S10) and sentence rules (S8)."""

from __future__ import annotations

import re

import pytest

from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages.vi.script import (
    VI_SENTENCE_RULES,
    VI_SPEAKER_PATTERN,
    VI_SUBTITLE_REGEX,
    VietnameseScript,
    is_vietnamese_word,
)
from anki_miner.languages.vi.syllables import COMMON_SYLLABLES
from anki_miner.services.reading.sentence_splitter import split_sentences

SCRIPT = VietnameseScript()
ENGLISH_COLLISIONS = {"a", "an", "the", "to", "do", "on", "so", "no", "me", "be", "can", "may"}


@pytest.mark.parametrize("text", ["không", "Hôm nay trời đẹp quá.", "ĐI ĐÂU", "bác sĩ", "đ"])
def test_a_vietnamese_letter_admits_the_text(text):
    assert SCRIPT.contains_target_script(text)


@pytest.mark.parametrize("text", ["toi khong biet", "anh co di khong", "ban"])
def test_unaccented_vietnamese_passes_on_its_syllables(text):
    assert SCRIPT.contains_target_script(text)


def test_a_two_syllable_unaccented_text_needs_both_syllables():
    """Judge finding 1: 1 >= 0.6 * 2 is false, so one table syllable out of two is not enough.

    ``on`` is one of the twelve English function words kept out of the table, so ``cam on``
    (thank you, unaccented) fails the text gate; ``toi khong`` passes with both in the table.
    The orchestrator ruled the gate is not weakened: it is the S15 defence against ingesting an
    English deck as Vietnamese.
    """
    assert not SCRIPT.contains_target_script("cam on")
    assert SCRIPT.contains_target_script("toi khong")


@pytest.mark.parametrize("text", ["the dog", "to go", "apple", "can", "I love you", "Lizzy", "OK", "", "2024", "..."])
def test_english_and_letterless_text_is_rejected(text):
    assert not SCRIPT.contains_target_script(text)


@pytest.mark.parametrize("text", ["không 日本", "사랑해", "ภาษาไทย", "مرحبا", "bác sĩ (博士)", "ありがとう"])
def test_a_cjk_hangul_thai_arabic_or_kana_character_fails_the_whole_text(text):
    assert not SCRIPT.contains_target_script(text)


def test_the_documented_collisions():
    """S15: a stripped syllable that is also an English word still reads as Vietnamese (ten = tên)."""
    assert SCRIPT.contains_target_script("ten")
    assert SCRIPT.contains_target_script("café")  # é is in the spec's class; Latin decks are the checklist's job


def test_the_syllable_table():
    assert len(COMMON_SYLLABLES) == 200
    assert all(word.isascii() and word.isalpha() and word.islower() for word in COMMON_SYLLABLES)
    assert not COMMON_SYLLABLES & ENGLISH_COLLISIONS
    assert {"toi", "khong", "nguoi", "duoc"} <= COMMON_SYLLABLES


def test_no_script_filter_options():
    assert SCRIPT.filter_options() == ()
    assert SCRIPT.matches("anything", "không") is False


@pytest.mark.parametrize(
    "word", ["mua", "tin", "xe", "phim", "khong", "truong", "nghieng", "bia", "bác sĩ", "Mua", "đẹp đẽ", "cá"]
)
def test_the_token_gate_admits_any_well_formed_vietnamese_word(word):
    """Decision 16: the parser's per-token gate. ``mua`` (buy) is ASCII and not in the 200-syllable set."""
    assert is_vietnamese_word(word)
    assert not SCRIPT.contains_target_script("mua")  # the text gate alone would drop it


@pytest.mark.parametrize(
    "word", ["OK", "ok", "Directionless", "internet", "hello", "yes", "love", "Parker", "2024", "?", "", "日本", "b"]
)
def test_the_token_gate_rejects_english_shapes_digits_and_foreign_script(word):
    assert not is_vietnamese_word(word)


@pytest.mark.parametrize("word", ["nhanh", "nhau", "mang", "quay", "phim", "mua"])
def test_the_measured_gate_divergence(word):
    """Judge finding 2, kept by orchestrator ruling and sized here.

    On 20,000 real OpenSubtitles cues, 682 of 7,720 distinct mineable fronts (8.8 %) and 2,689 of
    69,867 tokens (3.8 %) are ASCII-only Vietnamese words the per-token gate mines and the text
    gate rejects. They never enter get_existing_vocabulary or known_words.vi.db, so a learned word
    of this shape can be offered again. Aligning the two gates would ingest English decks as
    Vietnamese (S15), so the divergence stays and is recorded in the plan's Known limits.
    """
    assert is_vietnamese_word(word)
    assert not SCRIPT.contains_target_script(word)


@pytest.mark.parametrize(
    ("cue", "cleaned"),
    [
        ("ĐỨC: Chào anh.", "Chào anh."),
        ("NGƯỜI DẪN CHUYỆN: Ngày xưa có một ông vua.", "Ngày xưa có một ông vua."),
        ("[tiếng súng] Nằm xuống!", " Nằm xuống!"),
        ("- Anh ơi! - Gì thế?", "Anh ơi! Gì thế?"),
    ],
)
def test_the_vietnamese_sdh_preset(cue, cleaned):
    assert re.sub(VI_SUBTITLE_REGEX, "", cue) == cleaned


def test_the_shared_latin_preset_misses_vietnamese_capitals():
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "ĐỨC: Chào anh.") == "ĐỨC: Chào anh."
    assert VI_SPEAKER_PATTERN in VI_SUBTITLE_REGEX
    re.compile(VI_SUBTITLE_REGEX)  # no inline flags: a preset that cannot compile disables the filter


def test_abbreviations_do_not_end_a_sentence():
    text = "TS. Nguyễn Văn An sống ở Tp. Hồ Chí Minh. Anh ấy là bác sĩ."
    assert split_sentences(text, rules=VI_SENTENCE_RULES) == [
        "TS. Nguyễn Văn An sống ở Tp. Hồ Chí Minh.",
        "Anh ấy là bác sĩ.",
    ]


def test_terminators_split_and_the_rules_are_space_aware():
    assert split_sentences("Tôi đi. Anh ở lại! Sao vậy?", rules=VI_SENTENCE_RULES) == [
        "Tôi đi.",
        "Anh ở lại!",
        "Sao vậy?",
    ]
    assert VI_SENTENCE_RULES.space_aware and "…" in VI_SENTENCE_RULES.ellipses

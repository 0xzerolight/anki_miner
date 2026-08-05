"""Focused card-front identity tests."""

import pytest

from anki_miner.models.reading import ReadingUnit
from anki_miner.models.word import TokenizedWord, select_mined_form
from anki_miner.services.subtitle_parser import SubtitleParserService


@pytest.mark.parametrize(
    ("lemma", "surface", "pronunciation", "expected"),
    [
        ("手", "手ぇ", "テー", "手"),
        ("気", "気い", "キー", "気"),
        ("手", "手ー", "テー", "手"),
        ("舞", "舞い", "マイ", "舞い"),
    ],
)
def test_vowel_tail_selection_requires_elongated_pronunciation(
    lemma: str,
    surface: str,
    pronunciation: str,
    expected: str,
) -> None:
    assert (
        select_mined_form(
            "名詞",
            surface,
            lemma,
            surface,
            pronunciation=pronunciation,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("lemma", "surface", "pronunciation", "expected"),
    [
        ("手", "手ぇ", "テー", "手"),
        ("気", "気い", "キー", "気"),
        ("手", "手ー", "テー", "手"),
        ("舞", "舞い", "マイ", "舞い"),
    ],
)
def test_vowel_tail_tokenized_word_uses_same_pronunciation_evidence(
    lemma: str,
    surface: str,
    pronunciation: str,
    expected: str,
) -> None:
    word = TokenizedWord(
        surface=surface,
        lemma=lemma,
        reading=pronunciation,
        sentence=surface,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        orth_base=surface,
        pronunciation=pronunciation,
        pos="名詞",
    )

    assert word.mined_form == expected


@pytest.mark.parametrize(
    ("sentence", "surface", "pronunciation", "expected"),
    [
        ("手ぇを出せ", "手ぇ", "テー", "手"),
        ("気いつけて", "気い", "キー", "気"),
        ("華麗な舞いを披露した", "舞い", "マイ", "舞い"),
    ],
)
def test_parser_emits_pronunciation_used_by_mined_form(
    test_config,
    sentence: str,
    surface: str,
    pronunciation: str,
    expected: str,
) -> None:
    service = SubtitleParserService(test_config)
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="test")],
        want_line_index=False,
    )
    word = next(word for word in words if word.surface == surface)

    assert word.pronunciation == pronunciation
    assert word.mined_form == expected

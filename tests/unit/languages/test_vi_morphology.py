"""vi POS gate tables, the stopword list and the lookup-miss ladder (spec C.4) - engine-free."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages.vi.keys import vi_fold_term
from anki_miner.languages.vi.morphology import VietnameseLookup, reduplicative_base, y_to_i
from anki_miner.languages.vi.pos import (
    NAME_TAG,
    STOPWORD_TAG,
    VI_ALLOWED_POS,
    VI_EXCLUDED_SUBTYPES,
    VI_POS_LABELS,
)
from anki_miner.languages.vi.stopwords import VI_STOPWORDS

#: The words spec C.4 names for the list.
SPEC_STOPWORDS = {
    "tôi",
    "ta",
    "tao",
    "mình",
    "mày",
    "mi",
    "nó",
    "họ",
    "chúng",
    "bọn",
    "các",
    "anh",
    "chị",
    "em",
    "ông",
    "bà",
    "cô",
    "chú",
    "bác",
    "cháu",
    "con",
    "cậu",
    "mợ",
    "dì",
    "ạ",
    "nhé",
    "nha",
    "à",
    "ơi",
}
#: Content words a learner mines that share a spelling with a function word (or sit next to one).
MINEABLE = {"mẹ", "bố", "là", "đi", "về", "cái", "người", "bạn", "ông bà", "sao", "thôi", "nhiều", "đẹp", "ô"}


def test_the_gate_tables():
    assert VI_ALLOWED_POS == ("N", "V", "A", "Nc", "Nu")
    assert VI_EXCLUDED_SUBTYPES == (STOPWORD_TAG, NAME_TAG) == ("stopword", "name")
    assert set(VI_ALLOWED_POS) | set(VI_EXCLUDED_SUBTYPES) <= set(VI_POS_LABELS)
    assert not {"Np", "P", "R", "E", "C", "M", "L", "T", "I", "X", "CH", "Ny", "B"} & set(VI_ALLOWED_POS)


def test_the_stopword_list_is_curated_folded_and_leaves_content_words_alone():
    assert 120 <= len(VI_STOPWORDS) <= 160
    assert SPEC_STOPWORDS <= VI_STOPWORDS
    assert not MINEABLE & VI_STOPWORDS
    for word in VI_STOPWORDS:
        assert word == vi_fold_term(word) == unicodedata.normalize("NFC", word), word


@pytest.mark.parametrize(
    ("word", "variant"),
    [
        ("kỹ thuật", "kĩ thuật"),
        ("lý do", "lí do"),
        ("mỹ thuật", "mĩ thuật"),
        ("hy vọng", "hi vọng"),
        ("thời kỳ", "thời kì"),
        ("Kỹ", "Kĩ"),
    ],
)
def test_y_to_i_folds_a_sole_vowel_y_after_an_onset(word, variant):
    assert y_to_i(word) == variant


@pytest.mark.parametrize("word", ["quý", "thủy", "tay", "dây", "yêu", "y tá", "khuya", "ngoáy", "bác sĩ", ""])
def test_y_to_i_leaves_every_other_y(word):
    """Spec C.4: onset qu, and uy ay ây oay yê ya, keep their y; a bare y has no onset."""
    assert y_to_i(word) == ""


@pytest.mark.parametrize(("word", "base"), [("xanh xanh", "xanh"), ("từ từ", "từ")])
def test_a_full_reduplication_offers_its_syllable(word, base):
    assert reduplicative_base(word) == base


#: Partial reduplicatives are wty-vi-en headwords; the same-onset guess misread compounds (một mạch).
PARTIAL_OR_NONE = ["đẹp đẽ", "lung linh", "xinh xắn", "nhỏ nhắn", "một mạch", "bác sĩ", "bây giờ", "hòa bình"]


@pytest.mark.parametrize("word", [*PARTIAL_OR_NONE, "đẹp", "trần thị bích hằng", "ung thư"])
def test_other_words_have_no_reduplicative_base(word):
    assert reduplicative_base(word) == ""


def test_the_ladder_order_and_shape():
    lookup = VietnameseLookup()
    assert lookup.candidates("kỹ thuật", "Kỹ thuật", None) == [("kĩ thuật", 0)]
    assert lookup.candidates("xanh xanh", "", None) == [("xanh", 0)]
    assert lookup.candidates("đẹp đẽ", "", None) == []
    assert lookup.candidates("lý lẽ", "", None) == [("lí lẽ", 0)]
    assert lookup.candidates("ðẹp đẽ", "", None) == [("đẹp đẽ", 0)]
    assert lookup.candidates("bác sĩ", "Bác sĩ", None) == []

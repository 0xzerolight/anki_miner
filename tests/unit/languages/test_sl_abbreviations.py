"""SL_ABBREVIATIONS is derived from spaCy's Slovenian tokenizer exceptions, never hand-edited."""

from __future__ import annotations

import pytest

from anki_miner.languages.sl.abbreviations import SL_ABBREVIATIONS, SL_ORDINARY_WORDS


def _spacy_stems() -> set[str]:
    from spacy.lang.sl.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    return {key[:-1].casefold() for key in TOKENIZER_EXCEPTIONS if key.endswith(".") and any(c.isalpha() for c in key)}


def test_the_set_is_the_spacy_list_minus_the_ordinary_words():
    """Re-derives the shipped set: regenerate it, never hand-edit it (the ru/el precedent)."""
    stems = _spacy_stems()
    assert len(stems) == 1006
    assert stems >= SL_ORDINARY_WORDS
    assert stems - SL_ORDINARY_WORDS == SL_ABBREVIATIONS
    assert len(SL_ORDINARY_WORDS) == 178
    assert len(SL_ABBREVIATIONS) == 828


def test_every_key_is_casefolded_and_undotted_at_the_end():
    assert all(stem == stem.casefold() and not stem.endswith(".") for stem in SL_ABBREVIATIONS)
    # the multi-dot literals keep their inner dots and are never pruned by the tokenizer surgery
    assert {"t.i", "t.j", "d.o.o", "s.p", "d.d", "d.n.o", "l.r"} <= SL_ABBREVIATIONS


@pytest.mark.parametrize("word", ["je", "in", "na", "so", "ga", "v", "z", "film", "ok", "ur", "test"])
def test_an_ordinary_slovene_word_is_not_an_abbreviation(word):
    """NOTE 013: a kept stem glues its sentence-final dot and the word vanishes from the cards."""
    assert word in SL_ORDINARY_WORDS and word not in SL_ABBREVIATIONS


@pytest.mark.parametrize(
    "stem",
    # Written with their real diacritics, not ASCII look-alikes: spaCy's Slovenian exceptions hold
    # BOTH spellings as distinct stems, so pinning "st" would leave the real "št" unpinned.
    ["dr", "prof", "mag", "npr", "itd", "ipd", "oz", "tj", "št", "ul", "tel", "str", "gosp", "cca", "op", "inž", "tč"],
)
def test_a_standard_slovene_abbreviation_survives_the_cut(stem):
    assert stem in SL_ABBREVIATIONS


def test_the_diacritic_stems_are_the_real_letters():
    """A guard against an ASCII look-alike or a decomposed spelling creeping in."""
    assert {"št", "inž", "tč", "čl", "štud"} <= SL_ABBREVIATIONS
    assert all(not 0x300 <= ord(char) <= 0x36F for stem in SL_ABBREVIATIONS for char in stem)

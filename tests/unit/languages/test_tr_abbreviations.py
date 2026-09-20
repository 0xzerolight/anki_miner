"""Turkish sentence-splitter abbreviations (S8): spaCy's tr exceptions minus the ordinary words (plan decision 10)."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.tr.abbreviations import TR_ABBREVIATION_DROPS, TR_ABBREVIATIONS
from anki_miner.services.reading.sentence_splitter import split_sentences

RULES = sentence_rules(TR_ABBREVIATIONS)


def test_abbreviations_are_spacy_minus_the_word_keys():
    from spacy.lang.tr.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and any(c.isalpha() for c in text)
    }
    assert len(seeded) == 97
    assert seeded - TR_ABBREVIATION_DROPS == TR_ABBREVIATIONS
    assert {"av", "bul", "kur", "max", "min", "sok", "tel"} == TR_ABBREVIATION_DROPS <= seeded
    # The splitter's fold is str.casefold: a capital dotted I keeps its combining dot (İst. -> i + U+0307 + st).
    assert {"dr", "prof", "doç", "vb", "vs", "cad", "m.ö", "t.c", "i\u0307st"} <= TR_ABBREVIATIONS
    assert all(key == key.casefold() for key in TR_ABBREVIATIONS)


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Prof. Dr. Ayşe Yılmaz geldi. Tamam.", ["Prof. Dr. Ayşe Yılmaz geldi.", "Tamam."]),
        ("Kalem, defter vb. şeyler aldık. Sonra eve döndük.", ["Kalem, defter vb. şeyler aldık.", "Sonra eve döndük."]),
        ("İst. Üni. öğrencisiyim. Evet.", ["İst. Üni. öğrencisiyim.", "Evet."]),
        ("M.Ö. 500 yılında. Evet.", ["M.Ö. 500 yılında.", "Evet."]),
        ("Atatürk Cad. No 5. Orada.", ["Atatürk Cad. No 5.", "Orada."]),
        ("Onu bul. Tamam.", ["Onu bul.", "Tamam."]),  # a dropped key stays an ordinary word
    ],
)
def test_the_abbreviations_drive_the_splitter(text, sentences):
    assert split_sentences(text, rules=RULES) == sentences


def test_an_ordinal_still_ends_a_book_sentence():
    """Known miss (the de/hu case): a Turkish ordinal is a digit plus a full stop."""
    assert split_sentences("Saat 3. derste. Tamam.", rules=RULES) == ["Saat 3.", "derste.", "Tamam."]

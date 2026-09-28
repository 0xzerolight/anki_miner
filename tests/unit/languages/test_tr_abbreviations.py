"""Turkish sentence-splitter abbreviations (S8): spaCy's tr exceptions minus the ordinary words (plan decision 10),
plus the day numbers 1-31 (the da DA8 shape)."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.tr.abbreviations import TR_ABBREVIATION_ADDITIONS, TR_ABBREVIATION_DROPS, TR_ABBREVIATIONS
from anki_miner.services.reading.sentence_splitter import split_sentences

RULES = sentence_rules(TR_ABBREVIATIONS)


def test_abbreviations_are_spacy_minus_the_word_keys_plus_the_day_numbers():
    from spacy.lang.tr.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and any(c.isalpha() for c in text)
    }
    assert len(seeded) == 97
    assert (seeded - TR_ABBREVIATION_DROPS) | TR_ABBREVIATION_ADDITIONS == TR_ABBREVIATIONS
    assert {"av", "bul", "kur", "max", "min", "sok", "tel"} == TR_ABBREVIATION_DROPS <= seeded
    assert {str(day) for day in range(1, 32)} == TR_ABBREVIATION_ADDITIONS and not TR_ABBREVIATION_ADDITIONS & seeded
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
        # The accepted DA8 cost: a sentence ending in a bare day number runs on.
        ("Atatürk Cad. No 5. Orada.", ["Atatürk Cad. No 5. Orada."]),
        ("Onu bul. Tamam.", ["Onu bul.", "Tamam."]),  # a dropped key stays an ordinary word
    ],
)
def test_the_abbreviations_drive_the_splitter(text, sentences):
    assert split_sentences(text, rules=RULES) == sentences


@pytest.mark.parametrize(
    "text",
    ["Ailesi 2. Dünya Savaşı sırasında taşındı.", "Saat 3. derste.", "Dr. Ahmet Yılmaz, 19. yüzyılın sonunda doğdu."],
)
def test_an_ordinal_does_not_end_a_book_sentence(text):
    """A Turkish ordinal is a digit plus a full stop, and the noun after it may be capitalised."""
    assert split_sentences(text, rules=RULES) == [text]


def test_a_number_past_the_day_range_still_ends_a_sentence():
    assert split_sentences("Yıl 1990. Sonra taşındık.", rules=RULES) == ["Yıl 1990.", "Sonra taşındık."]

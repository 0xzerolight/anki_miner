"""Lithuanian books split on rules, never on the model (sents_f .79): abbreviations, quotes, soft hyphens."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.reading.sentence_splitter import split_sentences

EXCERPT = (Path(__file__).resolve().parents[2] / "fixtures" / "lt" / "book_excerpt.txt").read_text(encoding="utf-8")
#: The exact rule-based units of the fixture. Deterministic: no model is involved.
EXPECTED_UNITS = [
    "Prof. A. Jonaitis gimė 1950 m. Kaune.",
    "Jis rašė knygas, pvz., romanus ir apsakymus, t. y. daug prozos.",
    "„Ar tu skaitei mano knygą?“ – paklausė jis.",
    "Studentė atsakė: „Taip. Labai patiko!“ Vėliau jie kalbėjo apie XX a. literatūrą... Tai buvo įdomu.",
    "Knygos kainavo 5 tūkst. eurų ir t.t. Jis mirė 2010 m.",
]


@pytest.fixture(scope="module")
def parser():
    return get_profile("lt").create_parser(switch_language(AnkiMinerConfig(), "lt"))


def _units() -> list[str]:
    return split_sentences(EXCERPT, rules=get_profile("lt").sentence_rules)


def test_the_excerpt_splits_into_its_pinned_units():
    """Title abbreviations, a name initial, `t. y.`, `ir t.t.`, an ASCII ellipsis and `XX a.` all hold."""
    assert _units() == EXPECTED_UNITS


def test_no_unit_but_the_last_ends_on_an_abbreviation_dot():
    """A unit never breaks after an abbreviation. The final `2010 m.` ends the text, not a sentence."""
    rules = get_profile("lt").sentence_rules
    for unit in _units()[:-1]:
        last = unit.rstrip().rsplit(" ", 1)[-1]
        assert not (last.endswith(".") and last[:-1].casefold() in rules.abbreviations), unit


def test_a_sentence_final_ordinary_word_still_ends_the_unit():
    rules = get_profile("lt").sentence_rules
    assert split_sentences("Jis grįžo namo. Ji liko.", rules=rules) == ["Jis grįžo namo.", "Ji liko."]


def test_low_quotes_open_a_quotation_that_high_quotes_close():
    """`„` opens and `“` closes, so a terminator inside the quotation never ends the unit (the de shape)."""
    rules = get_profile("lt").sentence_rules
    assert "„" in rules.openers and "“" in rules.closers and "“" not in rules.openers
    assert split_sentences("Jis tarė: „Labas! Kaip sekasi?“ Ji tylėjo.", rules=rules) == [
        "Jis tarė: „Labas! Kaip sekasi?“ Ji tylėjo."
    ]


def test_an_epub_soft_hyphen_is_gone_before_the_word_is_mined(parser):
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text="Jis skaitė įdo\u00admią kny\u00adgą.", index=0, location_label="t")], False
    )
    mined = {word.mined_form for word in words}
    assert "knyga" in mined and not any("\u00ad" in front for front in mined)


def test_every_mined_front_of_the_excerpt_comes_from_its_own_unit(parser):
    for unit in EXPECTED_UNITS:
        words, _index, _counts = parser.parse_text_units([ReadingUnit(text=unit, index=0, location_label="t")], False)
        normalized = get_profile("lt").normalize(unit)
        assert all(word.surface in normalized for word in words), unit
        assert not any(front.endswith(".") for front in {word.mined_form for word in words}), unit

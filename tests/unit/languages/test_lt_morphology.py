"""Lithuanian data for the spaCy substrate (no model): stress fold, normalize, abbreviations, quotes, SDH regex."""

from __future__ import annotations

import re
import unicodedata

import pytest

from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages.lt.abbreviations import ALKSNIS, HAND, LETTERS, WIKTIONARY
from anki_miner.languages.lt.morphology import (
    LT_ABBREVIATIONS,
    LT_ALLOWED_POS,
    LT_CLOSERS,
    LT_EXCLUDED_SUBTYPES,
    LT_OPENERS,
    LT_SUBTITLE_REGEX,
    lt_normalize,
    strip_stress_marks,
)


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("knygà", "knyga"),
        ("knỹgos", "knygos"),
        ("ką\u0303", "ką"),
        ("pinigų\u0303", "pinigų"),
        ("pir\u0303k", "pirk"),
        ("vi\u0307\u0301enas", "vienas"),  # an accented i keeps a combining dot above
        ("Argenti\u0307\u0300na", "Argentina"),
        ("ėjė\u0301", "ėjė"),
        ("ąčęėįšųūž", "ąčęėįšųūž"),  # the letters' own diacritics never move
        ("ĄČĘĖĮŠŲŪŽ", "ĄČĘĖĮŠŲŪŽ"),
    ],
)
def test_stress_marks_go_and_letters_stay(written, plain):
    assert strip_stress_marks(written) == plain
    assert strip_stress_marks(plain) == plain  # idempotent: folded keys are folded again


def test_decomposed_input_folds_to_nfc():
    assert strip_stress_marks(unicodedata.normalize("NFD", "Knỹgą ėjo")) == "Knygą ėjo"


def test_normalize_strips_stress_nbsp_and_soft_hyphens_only():
    assert lt_normalize("Knỹgą\u00a0skaitau, nes ji įdo\u00admi.") == "Knygą skaitau, nes ji įdomi."
    assert lt_normalize(unicodedata.normalize("NFD", "Žąsis žaliuoja.")) == "Žąsis žaliuoja."


def _derived() -> frozenset[str]:
    keys = {part[:-1].casefold() for title in WIKTIONARY for part in re.findall(r"\S+\.", title)}
    keys |= {form.casefold() for form in ALKSNIS} | set(LETTERS) | set(HAND)
    return frozenset(keys)


def test_the_abbreviation_set_is_derived_from_its_sources():
    assert _derived() == LT_ABBREVIATIONS
    assert len(LT_ABBREVIATIONS) == 142
    assert {"m", "pvz", "t", "y", "prof", "tūkst", "t.t", "š.m", "t.y", "psl", "a"} <= LT_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in LT_ABBREVIATIONS)
    assert not {"sek", "lenk", "up", "ir", "ne", "tai", "kad"} & LT_ABBREVIATIONS


def test_the_pos_gate_excludes_no_fine_tag():
    assert LT_ALLOWED_POS == ("ADJ", "ADV", "NOUN", "VERB") and LT_EXCLUDED_SUBTYPES == ()


def test_lithuanian_quotes_open_low_and_close_high():
    assert "„" in LT_OPENERS and "“" in LT_CLOSERS and "“" not in LT_OPENERS
    assert {"(", "«"} <= LT_OPENERS and {")", "»", "”"} <= LT_CLOSERS


@pytest.mark.parametrize("cue", ["ŠARŪNAS: Labas vakaras.", "RŪTA: Labas vakaras.", "JONAS: Labas vakaras."])
def test_the_sdh_default_strips_speaker_labels_with_lithuanian_capitals(cue):
    assert re.sub(LT_SUBTITLE_REGEX, "", cue) == "Labas vakaras."


def test_the_latin_default_misses_them_and_the_other_parts_stay():
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "ŠARŪNAS: Labas vakaras.") == "ŠARŪNAS: Labas vakaras."
    assert " ".join(re.sub(LT_SUBTITLE_REGEX, "", "- Labas. - Sveikas. [durys] ♪").split()) == "Labas. Sveikas."

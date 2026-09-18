"""Croatian data and passes: the digraph fold, the tone fold, the fine-tag table, the short-infinitive repair."""

from __future__ import annotations

import importlib
import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from anki_miner.languages._spaced.script import nfc_normalize
from anki_miner.languages.hr.morphology import (
    HR_ABBREVIATIONS,
    HR_EXCLUDED_SUBTYPES,
    HR_MODEL_PACKAGE,
    hr_normalize,
    hr_short_infinitive_pass,
    hr_tone_fold,
)
from anki_miner.languages.token import LanguageToken

KEPT_CATEGORIES = ("A", "Nc", "R", "Vm")


def token(surface: str, lemma: str, pos1: str = "VERB", pos2: str = "Vmn") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, pos2=pos2, lemma=lemma)


def test_the_dje_letter_survives_normalisation():
    """The spec's d-bar fold is rejected: it lands 262 real wty-sh-en terms on a DIFFERENT term
    (Inđija -> Indija), orphans 2,766 more, and ``normalize`` output is also the stored card sentence."""
    assert hr_normalize("Đak je kupio đački priručnik.") == "Đak je kupio đački priručnik."
    assert hr_normalize("građanin") == "građanin"


@pytest.mark.parametrize(("text", "expected"), [("ǆep", "džep"), ("ǅep", "Džep"), ("ǄEP", "DŽEP")])
def test_the_digraph_ligatures_fold_to_the_standard_spelling(text: str, expected: str) -> None:
    """D7's ligature half, kept for compliance: 0 of 457,883 wty-sh-en terms and 0 of the 202,438,751
    OpenSubtitles occurrences carry one, and the map's output is the ordinary two-letter spelling."""
    assert hr_normalize(text) == expected


def test_a_line_without_a_ligature_is_the_shared_nfc_normaliser():
    line = "Student je jučer pročitao zanimljivu knjigu."
    assert hr_normalize(line) == nfc_normalize(line)
    assert hr_normalize("c\u030citati") == "čitati"  # a decomposed caron composes


@pytest.mark.parametrize(
    ("text", "expected"),
    [("proči\u0300tati", "pročitati"), ("knji\u030fga", "knjiga"), ("knji\u0311ga", "knjiga"),
     ("knji\u0304ga", "knjiga"), ("kr\u0300v", "krv"),
     ("čitati", "čitati"), ("ćemo", "ćemo"), ("šuma", "šuma"),
     ("žena", "žena"), ("đak", "đak")],
)  # fmt: skip
def test_the_tone_fold_drops_marks_from_vowels_and_r_and_keeps_every_letter(text: str, expected: str) -> None:
    """Measured head-line pairs: (U+0304, i) 3,747 and (U+0300, o) 3,695 lead; U+030C on c/s/z IS the letter."""
    assert hr_tone_fold(text) == expected


def test_a_mark_on_a_base_the_dictionary_never_uses_is_left_alone():
    """Over 457,883 wty-sh-en term rows a combining mark sits on schwa 0 times and U+0309 occurs 0 times,
    so neither joins the fold's tables and both come back untouched."""
    assert hr_tone_fold("pə\u030fs") == "pə\u030fs"
    # The fold still normalises: i + U+0309 composes to U+1EC9, mark intact, as NFC alone would.
    assert hr_tone_fold("knji\u0309ga") == unicodedata.normalize("NFC", "knji\u0309ga")


def test_the_fine_tag_table_is_the_models_own_label_set():
    meta = json.loads(
        (
            Path(importlib.import_module(HR_MODEL_PACKAGE).__file__).parent / f"{HR_MODEL_PACKAGE}-3.8.0" / "meta.json"
        ).read_text(encoding="utf-8")
    )
    labels = set(meta["labels"]["tagger"])
    assert set(HR_EXCLUDED_SUBTYPES) == {tag for tag in labels if not tag.startswith(KEPT_CATEGORIES)}
    assert len(HR_EXCLUDED_SUBTYPES) == 407 == len(set(HR_EXCLUDED_SUBTYPES))
    assert list(HR_EXCLUDED_SUBTYPES) == sorted(HR_EXCLUDED_SUBTYPES)
    assert not any(tag.startswith(KEPT_CATEGORIES) for tag in HR_EXCLUDED_SUBTYPES)


def test_the_abbreviations_keep_the_standard_ones_and_drop_the_ordinary_words():
    """svi (all) 108, im (to them) 188, pet (five) 425 and red (row) 913 are ordinary words in the
    OpenSubtitles list, so the May/Friday abbreviations they would stand for stay out of the set."""
    assert {"dr", "prof", "npr", "itd", "tzv", "god", "str", "br", "sv"} <= HR_ABBREVIATIONS
    assert not {"svi", "im", "pet", "red"} & HR_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in HR_ABBREVIATIONS)


def _attest(known: set[str]) -> tuple[Any, list[tuple[str, ...]]]:
    calls: list[tuple[str, ...]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(tuple(words))
        return {word for word in words if word in known}

    return attest, calls


def test_the_short_infinitive_takes_its_i_back_when_the_dictionary_knows_the_verb():
    tokens = [token("Morat", "morat"), token("ću", "htjeti", pos1="AUX", pos2="Var1s")]
    attest, calls = _attest({"morati"})
    assert hr_short_infinitive_pass(tokens, attest, None) is tokens
    assert tokens[0].feature.lemma == "morati"
    assert len(calls) == 1
    assert sorted(calls[0]) == ["morat", "morati"]


@pytest.mark.parametrize(
    ("surface", "lemma", "pos1", "known", "expected"),
    [
        # The three shapes the real tagger returns, with S2 on.
        ("mislit", "mislit", "VERB", {"misliti"}, "misliti"),  # lemma == surface
        ("imat", "imatti", "VERB", {"imati"}, "imati"),  # fabricated -tti lemma: the surface carries the candidate
        ("Morat", "morat", "VERB", {"morati"}, "morati"),  # S2 lowered it before the pass ran
        ("dat", "dat", "VERB", {"dat", "dati"}, "dat"),  # the model's own lemma is a term: never touched
        ("Gledat", "gledat", "NOUN", {"gledati"}, "gledat"),  # the POS gate: NOUN is not a verb
        ("Radit", "Radit", "PROPN", {"raditi"}, "Radit"),  # PROPN never mines, never repaired
        ("morat", "morat", "VERB", set(), "morat"),  # nothing attested
        ("knjiga", "knjiga", "VERB", {"knjigai"}, "knjiga"),  # the surface does not end in t: not a suspect
    ],
)
def test_the_repair_conditions(surface: str, lemma: str, pos1: str, known: set[str], expected: str) -> None:
    tokens = [token(surface, lemma, pos1=pos1)]
    attest, _calls = _attest(known)
    hr_short_infinitive_pass(tokens, attest, None)
    assert tokens[0].feature.lemma == expected


def test_without_a_dictionary_nothing_moves():
    tokens = [token("Morat", "morat")]
    hr_short_infinitive_pass(tokens, None, None)
    assert tokens[0].feature.lemma == "morat"

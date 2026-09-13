"""Spanish data tables and the pure enclitic helpers (spaCy's es exceptions are read for the abbreviation seed)."""

from __future__ import annotations

import pytest

from anki_miner.languages.es.morphology import (
    ENCLITIC_SLOTS,
    ES_ABBREVIATIONS,
    ES_ALLOWED_POS,
    ES_EXCLUDED_SUBTYPES,
    ES_GENDER_LABELS,
    ES_LEADING_WORDS,
    ES_MODEL_PACKAGE,
    IRREGULAR_IMPERATIVES,
    enclitic_splits,
    has_acute,
    strip_acute,
)

#: Ordinary Spanish words (and bare letters) that must never stop a sentence from ending.
NEVER = frozenset({"a", "e", "o", "y", "de", "la", "el", "sal", "ve", "mar", "fin"})


def _spacy_keys() -> set[str]:
    from spacy.lang.es.tokenizer_exceptions import TOKENIZER_EXCEPTIONS
    from spacy.lang.tokenizer_exceptions import BASE_EXCEPTIONS

    keys: set[str] = set()
    for text in TOKENIZER_EXCEPTIONS:
        if text in BASE_EXCEPTIONS or not text.endswith("."):
            continue
        for part in text.lstrip("0123456789").split(" "):
            if part.endswith(".") and len(part) > 1:
                keys.add(part[:-1].casefold())
    return keys


def test_the_pos_gate_and_the_model():
    assert ES_MODEL_PACKAGE == "es_core_news_sm"
    assert ES_ALLOWED_POS == ("ADJ", "ADV", "NOUN", "VERB")
    assert ES_EXCLUDED_SUBTYPES == ()


def test_abbreviations_are_spacys_plus_p():
    assert _spacy_keys() | {"p"} == ES_ABBREVIATIONS
    assert len(ES_ABBREVIATIONS) == 49
    assert {"sr", "sra", "srta", "dr", "dra", "ud", "uds", "ee", "uu", "ee.uu", "p", "ej", "p.ej", "etc", "m"} <= (
        ES_ABBREVIATIONS
    )
    assert not ES_ABBREVIATIONS & NEVER
    assert all(key == key.casefold() and not key.endswith(".") for key in ES_ABBREVIATIONS)


def test_leading_words_and_gender_labels():
    assert frozenset({"el", "la", "los", "las", "un", "una", "unos", "unas"}) == ES_LEADING_WORDS
    assert dict(ES_GENDER_LABELS) == {"masc": "el", "fem": "la"}


def test_the_enclitic_slot_table_and_the_irregular_imperatives():
    assert set(ENCLITIC_SLOTS) == {"se", "te", "os", "me", "nos", "lo", "la", "los", "las", "le", "les"}
    assert ENCLITIC_SLOTS["se"] < ENCLITIC_SLOTS["te"] < ENCLITIC_SLOTS["me"] < ENCLITIC_SLOTS["lo"]
    assert dict(IRREGULAR_IMPERATIVES) == {
        "di": "decir",
        "da": "dar",
        "haz": "hacer",
        "pon": "poner",
        "ten": "tener",
        "ven": "venir",
        "sal": "salir",
        "ve": "ir",
    }


def test_strip_acute_only_removes_the_acute_accent():
    assert strip_acute("dámelo") == "damelo"
    assert strip_acute("soñarlo") == "soñarlo"  # ñ keeps its tilde
    assert strip_acute("averigüe") == "averigüe"  # ü keeps its diaeresis
    assert has_acute("levántate") and not has_acute("levantarte") and not has_acute("niño")


@pytest.mark.parametrize(
    ("word", "splits"),
    [
        ("dámelo", [("dáme", ("lo",)), ("dá", ("me", "lo"))]),
        ("díselo", [("díse", ("lo",)), ("dí", ("se", "lo"))]),
        ("levantarse", [("levantar", ("se",))]),
        ("hermanos", [("herma", ("nos",))]),
        ("poderosos", [("poderos", ("os",))]),  # a clitic never repeats
        ("hábleme", [("háble", ("me",))]),  # le before me is not a valid chain
        ("tele", [("te", ("le",))]),
        ("se", []),  # a stem needs two characters
        ("casa", []),
    ],
)
def test_enclitic_splits_follow_the_slot_order(word, splits):
    assert enclitic_splits(word) == splits

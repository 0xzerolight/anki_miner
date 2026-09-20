"""Indonesian folds, dictionary keys and the lookup strategy (spec C.5, plan D3/D4)."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages.id.morphology import (
    ID_ABBREVIATIONS,
    IndonesianDictKeys,
    IndonesianLookupStrategy,
    id_fold,
    is_stopword,
)

KEYS = IndonesianDictKeys()
SAMPLES = [
    "Rumah",
    "MENGÉRTI",
    "Me\N{COMBINING ACUTE ACCENT}ngerti",
    "İstanbul",
    "Straße",
    "Buku-Buku",
    "Jum'at",
    "café",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_the_fold_is_idempotent_and_leaves_no_combining_mark(text):
    folded = id_fold(text)
    assert id_fold(folded) == folded
    assert "\N{COMBINING ACUTE ACCENT}" not in folded and "\N{COMBINING DOT ABOVE}" not in folded


def test_the_fold_is_casefold_then_mark_strip():
    assert [id_fold(text) for text in SAMPLES] == [
        "rumah",
        "mengerti",
        "mengerti",
        "istanbul",
        "strasse",
        "buku-buku",
        "jum'at",
        "cafe",
    ]


def test_the_keys_fold_terms_with_the_same_function_and_pass_readings_through():
    assert isinstance(KEYS, CasefoldDictKeys)
    assert KEYS.fold_term("Mengérti") == id_fold("Mengérti") == "mengerti"
    assert KEYS.fold_reading(None) is None and KEYS.fold_reading("x") == "x"


def test_casefold_dedup_makes_one_card():
    """``Rumah`` and ``rumah`` are one known word; a deck front's trailing dot is dropped too (D4)."""
    fold = spaced_dedup_fold(KEYS)
    assert fold("Rumah") == fold("rumah") == fold("rumah.") == "rumah"


def test_the_stopword_test_reads_through_the_curated_formal_spelling():
    assert is_stopword("yang") and is_stopword("yg") and is_stopword("nggak")
    assert not is_stopword("rumah") and not is_stopword("beliin")
    # content vocabulary stays mineable however common, and the read-through is core-only (D6)
    assert not is_stopword("waktu") and not is_stopword("membuat") and not is_stopword("bikin")


def test_the_strategy_folds_the_front_and_never_returns_it():
    lookup = IndonesianLookupStrategy()
    assert lookup.candidates("bukunya", "Bukunya", None) == [("buku", 0)]
    assert lookup.candidates("Bukunya", "", None) == [("buku", 0)]
    candidates = lookup.candidates("membeli", "", None)
    assert ("beli", 0) in candidates and ("membeli", 0) not in candidates
    assert all(conditions == 0 for _, conditions in candidates)
    assert lookup.candidates("sayur-mayur", "", None) == []


def test_the_abbreviations_are_casefolded_and_dotless():
    assert all(word == word.casefold() and not word.endswith(".") for word in ID_ABBREVIATIONS)
    assert {"dll", "dsb", "dr", "jl"} <= ID_ABBREVIATIONS

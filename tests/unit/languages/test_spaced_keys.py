"""Casefold dictionary keys (NFC + casefold, Rule A then A') and the S3 comparison fold."""

from __future__ import annotations

import importlib
import unicodedata

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold

KEYS = CasefoldDictKeys()
FOLD = spaced_dedup_fold(KEYS, frozenset({"a", "an", "the", "to"}))


def test_term_keys_are_nfc_casefold_and_symmetric():
    decomposed = unicodedata.normalize("NFD", "Café")
    assert KEYS.fold_term(decomposed) == KEYS.fold_term("café") == "café"
    assert KEYS.fold_term("Straße") == KEYS.fold_term("STRASSE") == "strasse"


def test_reading_keys_are_nfc_only():
    assert KEYS.fold_reading(None) is None
    assert KEYS.fold_reading("Ab") == "Ab"


def test_extra_fold_runs_before_casefold():
    keys = CasefoldDictKeys(extra_fold=lambda s: s.replace("ş", "ș").replace("Ş", "Ș"))
    assert keys.fold_term("Ştiinţă") == keys.fold_term("știinţă") == "știinţă"


def test_rule_a_keeps_term_exact_rows_and_their_content_duplicates():
    rows = [("go", "to move"), ("goes", "to move"), ("goes", "other")]
    assert KEYS.homograph_keep_mask("go", rows) == [True, True, False]


def test_rule_a_prime_scopes_to_the_lemma_when_no_term_row_exists():
    rows = [("left", "past of leave"), ("leave", "to depart")]
    assert KEYS.homograph_keep_mask("leaves", rows, "leave") == [False, True]


def test_no_exact_row_keeps_everything():
    rows = [("x", "a"), ("y", "b")]
    assert KEYS.homograph_keep_mask("z", rows, None) == [True, True]


@pytest.mark.parametrize(
    ("front", "folded"),
    [
        ("to go", "go"),
        ("To Go.", "go"),
        ("the dog", "dog"),
        ("the the dog", "dog"),
        ("to", "to"),
        ("the", "the"),
        ("to the", "the"),
        ("look up", "look up"),
        ("a b c d", "a b c d"),
        ("dog!", "dog"),
        ("e.g.", "e.g"),
        ("", ""),
    ],
)
def test_dedup_fold_drops_a_leading_article_only_when_something_remains(front, folded):
    assert FOLD(front) == folded


@pytest.mark.parametrize("text", ["to go", "The  Dog!", "the the the", "to.", "Café au lait ", "a b c d.", "…", "  "])
def test_dedup_fold_is_idempotent(text):
    assert FOLD(FOLD(text)) == FOLD(text)


CATALAN = spaced_dedup_fold(KEYS, frozenset({"el", "la", "l'", "els", "les"}))


@pytest.mark.parametrize(
    ("front", "folded"),
    [
        ("l'home", "home"),
        ("L’home", "home"),
        ("la casa", "casa"),
        ("l'", "l"),
        ("l'l'home", "home"),
        ("el l'home", "home"),
    ],
)
def test_a_glued_elided_article_is_stripped(front, folded):
    assert CATALAN(front) == folded
    assert CATALAN(CATALAN(front)) == CATALAN(front)


def test_mined_form_and_deck_front_meet():
    assert FOLD("go") == FOLD("to go")
    assert FOLD("House") == FOLD("the house") == "house"


#: Each shipped profile's OWN keys and table: fr builds CasefoldDictKeys(extra_fold=fold_apostrophes),
#: so a bare CasefoldDictKeys() would not cover it.
SHIPPED_TABLES = [
    ("ca", "CA_KEYS", "CA_LEADING_WORDS"),
    ("de", "DE_KEYS", "DE_LEADING_WORDS"),
    ("en", "EN_KEYS", "EN_LEADING_WORDS"),
    ("es", "ES_KEYS", "ES_LEADING_WORDS"),
    ("fr", "FR_KEYS", "FR_LEADING_WORDS"),
    ("it", "IT_KEYS", "IT_LEADING_WORDS"),
    ("nl", "NL_KEYS", "NL_LEADING_WORDS"),
    ("pt", "PT_KEYS", "PT_LEADING_WORDS"),
]


def test_table_entries_fold_like_the_text_they_are_compared_with():
    """casefold maps a final sigma to sigma: the entry ένας must still meet the folded front."""
    fold = spaced_dedup_fold(KEYS, frozenset({"ένας", "το"}))
    assert fold("Ένας φίλος") == fold("φίλος") == "φίλοσ"
    assert fold("ΤΟ ΒΙΒΛΙΟ") == "βιβλιο"


@pytest.mark.parametrize(("code", "keys_name", "table_name"), SHIPPED_TABLES)
def test_every_shipped_article_table_folds_to_itself(code, keys_name, table_name):
    """The fix is inert for the shipped tables: each folded through ITS OWN keys is unchanged."""
    module = importlib.import_module(f"anki_miner.languages.{code}")
    keys, table = getattr(module, keys_name), getattr(module, table_name)
    assert {keys.fold_term(word) for word in table} == set(table)

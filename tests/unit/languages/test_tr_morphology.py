"""Turkish folds, normaliser, POS map and SDH default (B.1, B.2, S3, S4, S5, S10) - engine-free."""

from __future__ import annotations

import re

import pytest

from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages.tr.morphology import (
    TR_DEDUP_FOLD,
    TR_KEYS,
    TR_SUBTITLE_REGEX,
    front_spelling,
    tr_casefold,
    tr_normalize,
    upos,
)

PAIRS = [
    ("IŞIK", "ışık"),
    ("İSTANBUL", "istanbul"),
    ("Irmak", "ırmak"),
    ("İyi", "iyi"),
    ("KİTAPLARI", "kitapları"),
    ("ÇOCUĞU", "çocuğu"),
    ("Şişe", "şişe"),
]


@pytest.mark.parametrize(("text", "folded"), PAIRS)
def test_tr_casefold_maps_the_capital_i_pair_the_turkish_way(text, folded):
    assert tr_casefold(text) == folded


def test_the_locale_blind_fold_gets_both_capitals_wrong():
    """Negative control: why tr_casefold exists (B.1)."""
    assert "IŞIK".casefold() == "işik"
    assert "İSTANBUL".casefold() == "i\u0307stanbul"


@pytest.mark.parametrize(("text", "folded"), PAIRS)
def test_index_keys_and_the_comparison_fold_use_the_same_letters(text, folded):
    """S4: one fold_term on the import and the query side; S3: known words, lists and dedup agree with it."""
    assert TR_KEYS.fold_term(text) == folded
    assert TR_DEDUP_FOLD(text) == folded


def test_both_folds_are_idempotent_and_compose_nfd():
    for text, _ in PAIRS:
        assert TR_KEYS.fold_term(TR_KEYS.fold_term(text)) == TR_KEYS.fold_term(text)
        assert TR_DEDUP_FOLD(TR_DEDUP_FOLD(text)) == TR_DEDUP_FOLD(text)
    assert TR_KEYS.fold_term("c\u0327ocuk") == "çocuk"


def test_the_comparison_fold_drops_trailing_punctuation_and_no_leading_word():
    assert TR_DEDUP_FOLD("Kitap!") == "kitap"
    assert TR_DEDUP_FOLD("bir kitap") == "bir kitap"  # no article table: bir is also the numeral "one"


def test_normalize_composes_and_cleans_but_keeps_apostrophes():
    assert tr_normalize("I\u0307stanbul’da\u00a0kal\u00adacak") == "İstanbul’da kalacak"


@pytest.mark.parametrize(
    ("primary", "secondary", "expected"),
    [
        ("Noun", "", "NOUN"),
        ("Verb", "", "VERB"),
        ("Adj", "", "ADJ"),
        ("Adv", "", "ADV"),
        ("Pron", "Pers", "PRON"),
        ("Postp", "PCDat", "ADP"),
        ("Conj", "", "CCONJ"),
        ("Det", "", "DET"),
        ("Num", "Card", "NUM"),
        ("Interj", "", "INTJ"),
        ("Ques", "", "PART"),
        ("Dup", "", "X"),
        ("Punc", "", "PUNCT"),
        ("Unk", "", "X"),
        ("Noun", "Prop", "PROPN"),
        ("Noun", "Abbrv", "X"),
    ],
)
def test_the_zeyrek_pos_map(primary, secondary, expected):
    assert upos(primary, secondary) == expected


def test_a_circumflex_lemma_follows_the_subtitle_spelling():
    assert front_spelling("ilâç", "ilacını") == "ilaç"
    assert front_spelling("kâğıt", "kâğıdı") == "kâğıt"


def _clean(pattern: str, cue: str) -> str:
    return " ".join(re.sub(pattern, "", cue).split())


@pytest.mark.parametrize(
    ("cue", "expected"),
    [
        ("AYŞE: Nereye gidiyorsun?", "Nereye gidiyorsun?"),
        ("İSMAİL: Geldim.", "Geldim."),
        ("DOĞAN: Tamam.", "Tamam."),
        ("[kapı çarpar] Kim o?", "Kim o?"),
        ("♪ Şarkı söylüyor ♪", "Şarkı söylüyor"),
        ("- Merhaba. - Selam.", "Merhaba. Selam."),
    ],
)
def test_the_turkish_sdh_default(cue, expected):
    assert _clean(TR_SUBTITLE_REGEX, cue) == expected


def test_the_latin_default_misses_turkish_speaker_labels():
    """Ğ, İ and Ş sit outside Latin-1: the reason tr carries its own speaker pattern (the hu precedent)."""
    assert _clean(LATIN_SUBTITLE_REGEX, "AYŞE: Nereye?") == "AYŞE: Nereye?"

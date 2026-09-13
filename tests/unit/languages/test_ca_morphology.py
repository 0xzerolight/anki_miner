"""Catalan data tables: normalize, the degraded-interpunct rung, the comparison fold, abbreviations."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.ca.morphology import (
    CA_ABBREVIATIONS,
    CA_ALLOWED_POS,
    CA_EXCLUDED_SUBTYPES,
    CA_GENDER_LABELS,
    CA_LEADING_WORDS,
    CA_MODEL_PACKAGE,
    ca_normalize,
    interpunct_variants,
)
from anki_miner.services.reading.sentence_splitter import split_sentences

#: The shared fold with the Catalan table: these cases pin the data, the mechanism is en's (contract item 12).
FOLD = spaced_dedup_fold(CasefoldDictKeys(), CA_LEADING_WORDS)


def test_the_pos_gate_and_model():
    assert CA_MODEL_PACKAGE == "ca_core_news_sm"
    assert CA_ALLOWED_POS == UPOS_ALLOWED
    assert CA_EXCLUDED_SUBTYPES == ()  # pos2 is dead: the model has no tagger (C5)
    assert dict(CA_GENDER_LABELS) == {"masc": "el", "fem": "la"}


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("coŀlegi", "col·legi"),  # U+0140
        ("COĿLEGI", "COL·LEGI"),  # U+013F
        ("col·legi", "col·legi"),  # U+00B7 survives: never NFKC
        (unicodedata.normalize("NFD", "això és"), "això és"),
        ("L’home d’aquell poble", "L’home d’aquell poble"),  # apostrophes stay verbatim
    ],
)
def test_normalize_composes_and_maps_the_legacy_l_dot_letters(text, normalized):
    assert ca_normalize(text) == normalized


@pytest.mark.parametrize(
    ("word", "surface", "variants"),
    [
        ("col.legi", "col.legi", ["col·legi"]),
        ("col-legi", "col-legi", ["col·legi"]),
        ("COL.LEGI", "COL.LEGI", ["COL·LEGI"]),
        ("colegi", "colegi", ["col·legi"]),
        # the lemma's variant leads: the full col·legi entry before the col·legis form-of stub (CA-2)
        ("colegi", "colegis", ["col·legi", "col·legis"]),
        ("tranquila", "tranquiles", ["tranquil·la", "tranquil·les"]),
        ("inteligent", "", ["intel·ligent"]),
        ("colegiala", "colegiala", ["col·legiala", "colegial·la"]),
        ("col·legi", "col·legis", []),  # already standard
        ("llibre", "llibres", []),  # no single l between letters
        ("sol", "sol", []),  # a final l has no right-hand letter
        ("nord-est", "nord-est", []),
        ("", "", []),
    ],
)
def test_the_rung_restores_a_degraded_interpunct(word, surface, variants):
    assert interpunct_variants(word, surface) == variants


@pytest.mark.parametrize(
    ("front", "folded"),
    [
        ("el llibre", "llibre"),
        ("La taula.", "taula"),
        ("L'home", "home"),
        ("l’home", "home"),
        ("l' home", "home"),
        ("les cases noves", "cases noves"),
        ("un gat", "gat"),
        ("en Pere", "pere"),
        ("la", "la"),
        ("d'acord", "d'acord"),
        ("Col·legi.", "col·legi"),
        ("llibre", "llibre"),
    ],
)
def test_the_fold_meets_the_mined_lemma(front, folded):
    assert FOLD(front) == folded


@pytest.mark.parametrize(
    "text", ["L'home", "l'l'home", "l'", "el l'home", "  ", "Les  Cases!", "d'acord", "l’home és savi"]
)
def test_the_fold_is_idempotent(text):
    assert FOLD(FOLD(text)) == FOLD(text)


def test_the_leading_word_table():
    assert frozenset({"el", "la", "l'", "l’", "els", "les", "un", "una", "en", "na"}) == CA_LEADING_WORDS


def test_abbreviations_come_from_spacy_catalan_minus_real_words():
    from spacy.lang.ca.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    spacy_keys = {text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and len(text) > 1}
    assert CA_ABBREVIATIONS - spacy_keys == {"núm"}  # spaCy lists núm undotted; it is written núm.
    assert "núm" in TOKENIZER_EXCEPTIONS
    assert not CA_ABBREVIATIONS & {"set", "a", "e", "i", "o", "u"}  # set (thirst, seven) ends sentences
    assert {"sr", "sra", "dr", "p.ex", "aprox", "pàg"} <= CA_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in CA_ABBREVIATIONS)


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("El Sr. Puig ha arribat. Anem-hi.", ["El Sr. Puig ha arribat.", "Anem-hi."]),
        ("Porta fruita, p.ex. pomes. Gràcies.", ["Porta fruita, p.ex. pomes.", "Gràcies."]),
        ("Tinc set. Vull aigua.", ["Tinc set.", "Vull aigua."]),
        ("Són les set. Anem.", ["Són les set.", "Anem."]),
    ],
)
def test_the_abbreviations_drive_the_splitter(text, sentences):
    assert split_sentences(text, rules=sentence_rules(CA_ABBREVIATIONS)) == sentences

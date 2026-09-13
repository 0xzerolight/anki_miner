"""Dutch data for the spaCy substrate (no model): candidates, hyphen repair, articles, normalise, abbreviations."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages.nl.abbreviations import NL_ABBREVIATION_ADDITIONS, NL_ABBREVIATION_DROPS, NL_ABBREVIATIONS
from anki_miner.languages.nl.morphology import (
    NL_ARTICLE_MAP,
    NL_EXCLUDED_SUBTYPES,
    NL_EXTRA_OPENERS,
    NL_GRAMMAR_SOURCES,
    NL_LEADING_WORDS,
    NL_SEPARABLE_PREFIXES,
    NL_SEPARABLE_VERB_DEPS,
    dutch_particle_candidates,
    nl_normalize,
    restore_compound_hyphens,
    restore_hyphens,
)
from anki_miner.languages.token import LanguageToken

FINITE = "Number=Sing|Tense=Pres|VerbForm=Fin"


def head(surface: str, lemma: str, particle: str, morph: str = FINITE) -> LanguageToken:
    token = LanguageToken(surface, "VERB", "WW|pv|tgw|ev", lemma, "", morph)
    token.feature.particle = particle
    return token


def test_the_pos_gate_and_the_dep_value():
    assert NL_EXCLUDED_SUBTYPES == ("TW|rang|nom|mv-n", "TW|rang|nom|zonder-n", "TW|rang|prenom|stan")
    assert frozenset({"compound:prt"}) == NL_SEPARABLE_VERB_DEPS


def test_prefixes_are_lowercase_and_longest_first():
    assert all(prefix == prefix.lower() for prefix in NL_SEPARABLE_PREFIXES)
    assert list(NL_SEPARABLE_PREFIXES) == sorted(NL_SEPARABLE_PREFIXES, key=lambda p: (-len(p), p))
    assert {"aan", "af", "mee", "op", "terug", "uit", "weg", "vooruit"} <= set(NL_SEPARABLE_PREFIXES)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (head("haal", "halen", "op"), ["ophalen"]),
        # the model joined the wrong particle: strip it before the plain join
        (head("bel", "terugbellen", "op"), ["opbellen", "opterugbellen"]),
        (head("zie", "inzien", "uit"), ["uitzien", "uitinzien"]),
        # the model joined the right particle already
        (head("bellen", "opbellen", "op", "VerbForm=Inf"), ["opbellen", "opopbellen"]),
        # an infinitive's surface is its lemma
        (head("gaan", "overgaan", "mee", "VerbForm=Inf"), ["meegaan", "meeovergaan"]),
        (head("leggen", "aanleggen", "uit", "VerbForm=Inf"), ["uitleggen", "uitaanleggen"]),
        # a leading prefix with fewer than four letters behind it is part of the verb (NL-3: bijten, not bij + ten)
        (head("bijt", "bijten", "af"), ["afbijten"]),
        (head("bijt", "bijten", "door"), ["doorbijten"]),
        (head("uit", "uiten", "op"), ["opuiten"]),
        # longest prefix first: vooruit, not voor
        (head("gaat", "vooruitgaan", "op"), ["opgaan", "opvooruitgaan"]),
    ],
)
def test_particle_candidates(token, expected):
    assert dutch_particle_candidates(token) == expected


@pytest.mark.parametrize(
    ("surface", "lemma", "expected"),
    [
        ("auto-ongeluk", "autoongeluk", "auto-ongeluk"),
        ("tv-programma", "tvprogramma", "tv-programma"),
        ("zee-egels", "zeeegel", "zee-egel"),
        ("Oud-leerling", "oudleerling", "oud-leerling"),
        ("e-mail", "e-mail", "e-mail"),  # the lemma kept its hyphen
        ("boek", "boek", "boek"),  # no hyphen
        ("hoofd-", "hoofd", "hoofd"),  # a truncated compound part, not a compound
        ("zee-egel", "egel", "egel"),  # the lemma no longer starts with the first part
    ],
)
def test_restore_hyphens(surface, lemma, expected):
    assert restore_hyphens(surface, lemma) == expected


def test_the_token_pass_repairs_every_lemma_in_place():
    tokens = [LanguageToken("auto-ongeluk", "NOUN", "", "autoongeluk"), LanguageToken("boek", "NOUN", "", "boek")]
    assert restore_compound_hyphens(tokens) is tokens
    assert [token.feature.lemma for token in tokens] == ["auto-ongeluk", "boek"]


def test_articles_and_sources():
    assert dict(NL_ARTICLE_MAP) == {"masc": "de", "fem": "de", "common": "de", "neut": "het"}
    assert NL_GRAMMAR_SOURCES == ("chips", "head", "morph")
    assert frozenset({"de", "het", "een", "'t", "’t", "zich"}) == NL_LEADING_WORDS
    assert frozenset("„") == NL_EXTRA_OPENERS


def test_normalize_composes_spaces_nbsp_drops_soft_hyphens_and_splits_the_ij_ligature():
    assert nl_normalize(unicodedata.normalize("NFD", "café één")) == "café één"
    assert nl_normalize("ge\u00adwoon\u00a0thuis") == "gewoon thuis"
    assert nl_normalize("\u0132s en \u0133zer") == "IJs en ijzer"
    assert nl_normalize("’t Is koud.") == "’t Is koud."


def test_abbreviations_are_spacy_minus_the_word_keys_plus_additions():
    from spacy.lang.nl.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and any(c.isalpha() for c in text)
    }
    assert (seeded - NL_ABBREVIATION_DROPS) | NL_ABBREVIATION_ADDITIONS == NL_ABBREVIATIONS
    assert seeded >= NL_ABBREVIATION_DROPS and not NL_ABBREVIATION_ADDITIONS & seeded
    assert all("." not in key for key in NL_ABBREVIATION_DROPS)
    kept = {"dhr", "mevr", "mr", "dr", "st", "jr", "mw", "bijv", "o.a", "d.w.z", "enz", "blz", "nr", "ca", "m.i"}
    assert kept <= NL_ABBREVIATIONS
    words = {"al", "dat", "pas", "vol", "me", "ov", "kon", "hand", "red", "volg", "it", "no", "sir", "a", "t"}
    assert not words & NL_ABBREVIATIONS
    assert all(key == key.casefold() for key in NL_ABBREVIATIONS)

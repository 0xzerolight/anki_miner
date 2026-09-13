"""German data tables: POS gate, abbreviations, fold words, separable-verb data, token pass, quotes, gender labels."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.morphology import LatinLookupStrategy
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.de.morphology import (
    DE_ABBREVIATIONS,
    DE_ALLOWED_POS,
    DE_CLOSERS,
    DE_EXCLUDED_SUBTYPES,
    DE_GENDER_LABELS,
    DE_LEADING_WORDS,
    DE_MODEL_PACKAGE,
    DE_OPENERS,
    DE_SEPARABLE_PREFIXES,
    SEPARABLE_VERB_DEPS,
    adjd_as_adjective,
    particle_less_verb,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.morphology import TokenInclusionRule

#: The script gate is part of the non-ja rule: without it should_include falls through to the kanji check.
RULE = TokenInclusionRule(
    allowed_pos=frozenset(DE_ALLOWED_POS),
    excluded_subtypes=frozenset(DE_EXCLUDED_SUBTYPES),
    script_gate=LatinScript().contains_target_script,
)


def test_the_pos_gate_is_upos_plus_the_derived_stts_table():
    assert DE_MODEL_PACKAGE == "de_core_news_sm"
    assert DE_ALLOWED_POS == UPOS_ALLOWED
    assert DE_EXCLUDED_SUBTYPES == ("CARD", "ITJ", "NE", "PTKANT", "PTKVZ", "TRUNC", "XY")


@pytest.mark.parametrize(
    ("surface", "pos1", "pos2", "mined"),
    [
        ("Hund", "NOUN", "NN", True),
        ("schön", "ADJ", "ADJD", True),
        ("deshalb", "ADV", "PROAV", True),
        ("warum", "ADV", "PWAV", True),
        ("sieht", "VERB", "VVFIN", True),
        ("HUND", "NOUN", "NE", False),  # a caps line the model read as a name
        ("zurück", "ADV", "PTKVZ", False),  # an unattached separable particle
        ("Bitte", "NOUN", "PTKANT", False),
        ("zweitausend", "NOUN", "CARD", False),
        ("Ein-", "NOUN", "TRUNC", False),
        ("Mann", "NOUN", "ITJ", False),
        ("as", "NOUN", "XY", False),
        ("an", "PART", "PTKVZ", False),  # the stash demotes a consumed particle to PART
        ("der", "DET", "ART", False),
    ],
)
def test_the_inclusion_gate_matches_the_de_defaults(surface, pos1, pos2, mined):
    assert RULE.should_include(LanguageToken(surface=surface, pos1=pos1, pos2=pos2, lemma=surface)) is mined


def test_abbreviations_are_spacys_german_exceptions_minus_common_words():
    from spacy.lang.de.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    spacy_keys = {
        text[:-1].casefold()
        for text in TOKENIZER_EXCEPTIONS
        if text.endswith(".") and len(text) > 1 and any(ch.isalpha() for ch in text)
    }
    assert spacy_keys - DE_ABBREVIATIONS == {"so", "max", "jan"}
    assert spacy_keys >= DE_ABBREVIATIONS
    assert {"z.b", "bzw", "usw", "dr", "prof", "nr", "str", "ca", "evtl", "d.h", "u.a", "z", "b"} <= DE_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in DE_ABBREVIATIONS)


def test_leading_words_and_separable_data():
    assert frozenset({"der", "die", "das", "sich"}) == DE_LEADING_WORDS
    assert frozenset({"svp"}) == SEPARABLE_VERB_DEPS
    assert list(DE_SEPARABLE_PREFIXES) == sorted(DE_SEPARABLE_PREFIXES, key=lambda p: (-len(p), p))
    assert {"an", "auf", "aus", "ab", "ein", "zu", "zurück", "weg", "los"} <= set(DE_SEPARABLE_PREFIXES)


@pytest.mark.parametrize(
    ("word", "surface", "expected"),
    [
        ("ansehen", "ansehen", ["sehen"]),
        ("ansehen", "anzusehen", ["sehen"]),  # the rung reads the lemma, never the surface (anzusehen -> zusehen)
        ("zurückkommen", "kommt", ["kommen", "rückkommen"]),
        ("einladen", "lade", ["laden"]),
        ("sehen", "sieht", []),  # no prefix leaves a four-letter stem
        ("Anruf", "Anruf", []),  # capitalised: a noun, never a particle verb
        ("ansicht", "ansicht", []),  # not an infinitive shape
        ("sah", "sah", []),
    ],
)
def test_the_particle_less_verb_rung(word, surface, expected):
    assert particle_less_verb(word, surface) == expected


def test_the_rung_sits_in_the_latin_ladder_and_never_follows_the_surface():
    ladder = LatinLookupStrategy(extra_rungs=(particle_less_verb,))
    candidates = ladder.candidates("ansehen", "anzusehen", None)
    assert candidates == [("anzusehen", 0), ("sehen", 0)]
    assert ("zusehen", 0) not in candidates
    assert ladder.candidates("ansehen", "ansehen", None) == [("sehen", 0)]
    assert ladder.candidates("sah", "", None) == []


def test_predicative_adjectives_become_adjectives():
    leer = LanguageToken(surface="leer", pos1="ADV", pos2="ADJD", lemma="leer")
    gern = LanguageToken(surface="gern", pos1="ADV", pos2="ADV", lemma="gern")
    gross = LanguageToken(surface="großer", pos1="ADJ", pos2="ADJA", lemma="groß")
    tokens = [leer, gern, gross]
    assert adjd_as_adjective(tokens) is tokens
    assert [token.feature.pos1 for token in tokens] == ["ADJ", "ADV", "ADJ"]
    assert [token.feature.pos2 for token in tokens] == ["ADJD", "ADV", "ADJA"]


def test_german_quote_pairs_and_gender_labels():
    assert set("„‚»›") <= DE_OPENERS and set("“‘«‹") <= DE_CLOSERS
    assert not DE_OPENERS & DE_CLOSERS
    assert "“" not in DE_OPENERS  # the English opener is the German closer
    assert dict(DE_GENDER_LABELS) == {"masc": "der", "fem": "die", "neut": "das"}

"""Hungarian data for the spaCy substrate (no model): preverbs, the -e clitic, quotes, speakers, abbreviations."""

from __future__ import annotations

import dataclasses
import re

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import SeparableVerbPass
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.hu.abbreviations import HU_ABBREVIATION_DROPS, HU_ABBREVIATIONS
from anki_miner.languages.hu.morphology import (
    HU_CLOSERS,
    HU_EXCLUDED_SUBTYPES,
    HU_LEADING_WORDS,
    HU_OPENERS,
    HU_PREVERB_DEPS,
    HU_PREVERBS,
    HU_SUBTITLE_REGEX,
    demote_question_clitic,
    hungarian_preverb_candidates,
    preverb_less_verb,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.reading.sentence_splitter import split_sentences

RULES = dataclasses.replace(sentence_rules(HU_ABBREVIATIONS), openers=HU_OPENERS, closers=HU_CLOSERS)


def head(lemma: str, particle: str) -> LanguageToken:
    token = LanguageToken("olvasta", "VERB", "", lemma, "", "VerbForm=Fin")
    token.feature.particle = particle
    return token


def test_the_pos_gate_and_the_dep_value():
    assert HU_EXCLUDED_SUBTYPES == ()
    assert frozenset({"compound:preverb"}) == HU_PREVERB_DEPS


def test_the_preverb_table_is_the_wiktionary_appendix():
    assert len(HU_PREVERBS) == 95
    assert {"meg", "el", "be", "ki", "fel", "föl", "le", "haza", "vissza", "össze", "észre", "tönkre"} <= HU_PREVERBS
    assert all(preverb == preverb.casefold() and not preverb.endswith("-") for preverb in HU_PREVERBS)
    assert not {"volna", "legalább", "hogy", "”", "nem", "neked"} & HU_PREVERBS


@pytest.mark.parametrize(
    ("lemma", "particle", "expected"),
    [
        ("olvas", "el", ["elolvas"]),
        ("vesz", "észre", ["észrevesz"]),
        ("megy", "haza", ["hazamegy"]),
        ("tör", "volna", []),
        ("vár", "”", []),
    ],
)
def test_only_a_listed_preverb_offers_a_join(lemma, particle, expected):
    assert hungarian_preverb_candidates(head(lemma, particle)) == expected


def test_without_a_dictionary_an_unlisted_particle_keeps_the_lemma():
    heads = [head("olvas", "el"), head("tör", "volna")]
    SeparableVerbPass(candidates=hungarian_preverb_candidates)(heads, None, None)
    assert [token.feature.lemma for token in heads] == ["elolvas", "tör"]
    assert not any(token.feature.particle for token in heads)


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("elkap", ["kap"]),
        ("hazamegy", ["megy"]),
        ("előrefut", ["fut", "refut", "őrefut"]),
        ("megy", []),  # meg + y: one letter is no verb
        ("olvas", []),
    ],
)
def test_the_rung_strips_a_preverb_from_the_front_longest_first(word, expected):
    assert preverb_less_verb(word, "ignored") == expected


def test_the_question_clitic_becomes_a_particle():
    tokens = [
        LanguageToken("TUDOD", "VERB", "", "tud", ""),
        LanguageToken("-E", "ADV", "", "-e", ""),
        LanguageToken("hol", "ADV", "", "hol", ""),
    ]
    assert [token.feature.pos1 for token in demote_question_clitic(tokens)] == ["VERB", "PART", "ADV"]


def test_the_leading_word_fold_and_the_double_acute_keys():
    fold = spaced_dedup_fold(CasefoldDictKeys(), HU_LEADING_WORDS)
    assert fold("a ház") == fold("Ház") == "ház"
    assert fold("az alma") == "alma" and fold("egy könyv") == "könyv" and fold("egy") == "egy"
    keys = CasefoldDictKeys()
    assert keys.fold_term("ŰRHAJÓ") == "űrhajó" and keys.fold_term("Őr") == "őr"
    assert keys.fold_term("u\u030brhajo\u0301") == "űrhajó"  # NFD input composes
    assert keys.fold_term("űrhajó") != keys.fold_term("urhajo")


def test_abbreviations_are_spacy_minus_the_word_keys():
    from spacy.lang.hu.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and any(c.isalpha() for c in text)
    }
    assert seeded - HU_ABBREVIATION_DROPS == HU_ABBREVIATIONS
    assert seeded >= HU_ABBREVIATION_DROPS and all("." not in key for key in HU_ABBREVIATION_DROPS)
    assert {"pl", "stb", "dr", "kb", "ill", "ún", "vö", "ld", "i.sz", "kr.e"} <= HU_ABBREVIATIONS
    assert not {"a", "be", "de", "ma", "út", "ti", "üdv", "adj", "fej"} & HU_ABBREVIATIONS
    assert all(key == key.casefold() for key in HU_ABBREVIATIONS)


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Hozz gyümölcsöt, pl. almát. Aztán gyere be.", ["Hozz gyümölcsöt, pl. almát.", "Aztán gyere be."]),
        ("Dr. Kovács kb. öt percet késett. Nem baj.", ["Dr. Kovács kb. öt percet késett.", "Nem baj."]),
        ("Gyere be. Ma nem jövök.", ["Gyere be.", "Ma nem jövök."]),
        ("Ez egy jó út. Menjünk.", ["Ez egy jó út.", "Menjünk."]),
    ],
)
def test_the_abbreviations_drive_the_splitter(text, sentences):
    assert split_sentences(text, rules=RULES) == sentences


def test_hungarian_quotes_hold_their_sentences():
    outer = "„Menj el. Most.” mondta. Aztán elment."
    assert split_sentences(outer, rules=RULES) == ["„Menj el. Most.” mondta.", "Aztán elment."]
    nested = "„Azt mondta: »Gyere. Most.« Aztán elment.” mondta Péter. Vége."
    assert split_sentences(nested, rules=RULES) == [
        "„Azt mondta: »Gyere. Most.« Aztán elment.” mondta Péter.",
        "Vége.",
    ]
    latin = sentence_rules(HU_ABBREVIATIONS)
    dutch_shape = dataclasses.replace(latin, openers=latin.openers | {"„"})
    assert len(split_sentences(nested, rules=dutch_shape)) == 3  # a shared » closer pops the open „ early


@pytest.mark.parametrize(
    ("line", "cleaned"),
    [
        ("GYŐZŐ: Hol vagy?", "Hol vagy?"),
        ("ŐR: Megállj!", "Megállj!"),
        ("PÉTER: Jó.", "Jó."),
        ("- Hol vagy? - Itt.", "Hol vagy? Itt."),
        ("[ajtó csapódik] ♪ Szia", "Szia"),
    ],
)
def test_the_hungarian_sdh_default_strips_labels_with_a_double_acute(line, cleaned):
    assert re.sub(HU_SUBTITLE_REGEX, "", line).strip() == cleaned


def test_the_latin_default_misses_those_labels():
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "GYŐZŐ: Hol vagy?").startswith("GYŐZŐ:")

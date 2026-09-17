"""Norwegian Bokmål data for the spaCy substrate (no model): candidates, articles, normalise, dashes, abbreviations."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages._spaced.script import DIALOGUE_DASH_PATTERN, LATIN_SUBTITLE_REGEX
from anki_miner.languages.nb.abbreviations import NB_ABBREVIATION_DROPS, NB_ABBREVIATIONS
from anki_miner.languages.nb.morphology import (
    NB_ALLOWED_POS,
    NB_ARTICLE_MAP,
    NB_DIALOGUE_DASH_PATTERN,
    NB_EXCLUDED_SUBTYPES,
    NB_LEADING_WORDS,
    NB_MODEL_PACKAGE,
    NB_SEPARABLE_VERB_DEPS,
    NB_SUBTITLE_REGEX,
    nb_normalize,
    norwegian_particle_candidates,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter


def head(lemma: str, particle: str) -> LanguageToken:
    token = LanguageToken(lemma, "VERB", "", lemma, "", "Mood=Ind|Tense=Pres|VerbForm=Fin")
    token.feature.particle = particle
    return token


def test_the_model_the_pos_gate_and_the_dep_value():
    assert NB_MODEL_PACKAGE == "nb_core_news_sm"
    assert NB_ALLOWED_POS == ("ADJ", "ADV", "NOUN", "VERB")
    assert NB_EXCLUDED_SUBTYPES == ()  # no tagger: pos2 is always "" (D11)
    assert frozenset({"compound:prt"}) == NB_SEPARABLE_VERB_DEPS


@pytest.mark.parametrize(
    ("lemma", "particle", "expected"),
    [
        ("stå", "opp", ["stå opp"]),
        ("laste", "ned", ["laste ned"]),
        ("finne", "på", ["finne på"]),
        # an abbreviation or a number can carry the arc; it is not a particle
        ("begynne", "kl.", []),
        ("begynne", "9", []),
    ],
)
def test_particle_candidates_write_the_verb_apart(lemma, particle, expected):
    assert norwegian_particle_candidates(head(lemma, particle)) == expected


def test_articles_and_leading_words():
    assert dict(NB_ARTICLE_MAP) == {"masc": "en", "fem": "ei", "neut": "et"}
    assert frozenset({"en", "ei", "et", "å"}) == NB_LEADING_WORDS


def test_normalize_composes_spaces_nbsp_and_drops_soft_hyphens():
    assert nb_normalize(unicodedata.normalize("NFD", "blåbær og brød")) == "blåbær og brød"
    assert nb_normalize("ge\N{SOFT HYPHEN}nialt\N{NO-BREAK SPACE}nå") == "genialt nå"
    assert nb_normalize("Æ Ø Å æ ø å") == "Æ Ø Å æ ø å"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("-Kom hit. -Nei, jeg vil sove.", "Kom hit. Nei, jeg vil sove."),
        ("- Kom hit. - Nei.", "Kom hit. Nei."),
        ("–Hvor er boka? –På bordet.", "Hvor er boka? På bordet."),
        ("Har du lest e-posten?", "Har du lest e-posten?"),
        ("-5 grader i dag.", "-5 grader i dag."),
        ("Hun sa – ja.", "Hun sa – ja."),
    ],
)
def test_the_norwegian_dash_rule_takes_an_unspaced_speaker_hyphen(line, expected):
    assert compile_subtitle_regex_filter(NB_DIALOGUE_DASH_PATTERN, "").sub("", line) == expected


def test_the_latin_rule_misses_the_unspaced_hyphen():
    assert compile_subtitle_regex_filter(DIALOGUE_DASH_PATTERN, "").sub("", "-Kom hit. -Nei.") == "-Kom hit. -Nei."


def test_the_subtitle_default_is_the_latin_one_with_the_norwegian_dash():
    assert LATIN_SUBTITLE_REGEX.replace(DIALOGUE_DASH_PATTERN, NB_DIALOGUE_DASH_PATTERN) == NB_SUBTITLE_REGEX
    compiled = compile_subtitle_regex_filter(NB_SUBTITLE_REGEX, "")
    # A dash that FOLLOWS a speaker label is out of this rule's reach: the speaker
    # pattern consumes "OLA: " first, so the dash is no longer at the cue start
    # (recorded as a known limit, B22 — fixing it means editing the shared pattern).
    assert compiled.sub("", "OLA: -Hvor er du? [dør smeller] ♪").strip() == "-Hvor er du?"
    assert compiled.sub("", "-Hvor er du? [dør smeller]").strip() == "Hvor er du?"


def test_abbreviations_are_spacy_minus_the_word_keys():
    from spacy.lang.nb.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and any(c.isalpha() for c in text)
    }
    assert seeded - NB_ABBREVIATION_DROPS == NB_ABBREVIATIONS
    assert seeded >= NB_ABBREVIATION_DROPS and all("." not in key for key in NB_ABBREVIATION_DROPS)
    assert len(NB_ABBREVIATIONS) == 167
    kept = {"kl", "ca", "dvs", "osv", "f.eks", "bl.a", "nr", "dr", "hr", "kr", "pga", "mnd", "evt", "m.a.o", "t.o.m"}
    assert kept <= NB_ABBREVIATIONS
    words = {"min", "ti", "to", "jul", "sen", "lat", "et", "sms", "no", "jan", "a", "i", "s"}
    assert not words & NB_ABBREVIATIONS
    assert all(key == key.casefold() for key in NB_ABBREVIATIONS)

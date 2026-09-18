"""The Polish tokenizer over the real model: surfaces, live NKJP tags, the three pl repairs."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.pl import tokenizer
from anki_miner.languages.pl.morphology import PL_ABBREVIATIONS, PL_MODEL_PACKAGE, PL_POST_PASSES
from anki_miner.services.tagger import LockedTagger


@pytest.fixture(scope="module")
def polish():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return tokenizer.build_tagger()


@pytest.fixture(scope="module")
def without_cases():
    """Negative control: the same adapter without pl's dotted-abbreviation special cases."""
    return build_spacy_tagger(PL_MODEL_PACKAGE, post_passes=PL_POST_PASSES, abbreviations=PL_ABBREVIATIONS)


def _by_surface(tagger, line):
    return {token.surface: token for token in tagger(line)}


def test_a_locked_tagger_without_the_parser(polish):
    assert isinstance(polish, LockedTagger)
    assert polish.nlp.pipe_names == ["tok2vec", "morphologizer", "lemmatizer", "tagger", "attribute_ruler"]


@pytest.mark.parametrize(
    "line",
    [
        "Książki leżą na stole.",
        "Spotkanie jest o godz. 18 przy ul. Długiej, np. jutro itd.",
        "„Chodź tutaj!” – krzyknęła mama.",
        "Wiedziałam—nie, nic nie wiedziałam.",
        "KONIEC FILMU",
        "Byłem w domu i czekałem.",
    ],
)
def test_surfaces_cover_the_line(polish, line):
    assert "".join(token.surface for token in polish(line)) == line.replace(" ", "")


def test_pos2_is_the_live_nkjp_tag_and_morph_is_kept(polish):
    tokens = _by_surface(polish, "Książki leżą na stole.")
    assert (tokens["Książki"].feature.pos1, tokens["Książki"].feature.pos2, tokens["Książki"].feature.lemma) == (
        "NOUN",
        "SUBST",
        "książka",
    )
    assert (tokens["leżą"].feature.pos2, tokens["leżą"].feature.lemma) == ("FIN", "leżeć")
    assert tokens["stole"].feature.lemma == "stół" and "Gender=Masc" in tokens["stole"].morph


@pytest.mark.parametrize(
    ("line", "surface", "lemma"),
    [
        ("Wczoraj widziałem go w kinie.", "widziałem", "widzieć"),
        ("Chciałbym kupić nowy samochód.", "Chciałbym", "chcieć"),
    ],
)
def test_an_agglutinated_verb_fronts_the_verb(polish, line, surface, lemma):
    token = _by_surface(polish, line)[surface]
    assert (token.feature.pos1, token.feature.lemma) == ("VERB", lemma)


def test_a_plurale_tantum_carries_no_gender(polish):
    drzwi = _by_surface(polish, "Drzwi były otwarte.")["Drzwi"]
    assert "Number=Ptan" in drzwi.morph and "Gender=" not in drzwi.morph


@pytest.mark.parametrize(
    ("line", "abbreviations"),
    [
        ("Spotkanie jest o godz. 18 przy ul. Długiej, np. jutro itd.", ["godz.", "ul.", "np.", "itd."]),
        ("Otwórz książkę na str. 12.", ["str."]),
        ("Godz. 18, Str. 5", ["Godz.", "Str."]),
    ],
)
def test_dotted_abbreviations_stay_whole_and_are_not_vocabulary(polish, line, abbreviations):
    tokens = _by_surface(polish, line)
    assert [tokens[surface].feature.pos1 for surface in abbreviations] == ["X"] * len(abbreviations)


def test_without_the_cases_the_split_abbreviation_would_be_a_noun(without_cases):
    assert _by_surface(without_cases, "Otwórz książkę na str. 12.")["str"].feature.pos1 == "NOUN"


def test_an_ordinary_word_before_a_final_dot_keeps_its_class(polish):
    assert [(token.surface, token.feature.pos1) for token in polish("OK. Powiedz im.")][-2:] == [
        ("im", "PRON"),
        (".", "PUNCT"),
    ]


def test_a_past_tense_line_tokenizes_into_words(polish):
    tokens = polish("Byłem w domu i czekałem.")
    assert [token.surface for token in tokens] == ["Byłem", "w", "domu", "i", "czekałem", "."]
    assert [tokens[0].feature.lemma, tokens[4].feature.lemma] == ["być", "czekać"]

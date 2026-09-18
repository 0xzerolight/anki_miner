"""The Lithuanian tokenizer over the real model: pipeline, abbreviation special cases, surfaces, casing, morph."""

from __future__ import annotations

import pytest

from anki_miner.languages.lt import tokenizer
from anki_miner.languages.lt.morphology import LT_ABBREVIATIONS
from anki_miner.services.tagger import LockedTagger


@pytest.fixture(scope="module")
def lithuanian():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return tokenizer.build_tagger()


def _by_surface(tagger, line):
    return {token.surface: token for token in tagger(line)}


def test_a_locked_tagger_without_parser_or_senter(lithuanian):
    """Model sentence boundaries (sents_f .79) never reach the app: books split with SentenceRules."""
    assert isinstance(lithuanian, LockedTagger)
    assert lithuanian.nlp.pipe_names == ["tok2vec", "morphologizer", "tagger", "lemmatizer", "attribute_ruler"]


def test_every_key_gets_its_lower_capitalised_and_upper_spelling():
    assert tokenizer.abbreviation_spellings(frozenset({"pvz", "t.t", "š"})) == sorted(
        {"pvz.", "Pvz.", "PVZ.", "t.t.", "T.t.", "T.T.", "š.", "Š."}
    )
    assert len(tokenizer.abbreviation_spellings(LT_ABBREVIATIONS)) > len(LT_ABBREVIATIONS)


@pytest.mark.parametrize(
    ("line", "abbreviations"),
    [
        ("Jis gimė 1990 m. Vilniuje.", ["m."]),
        ("Pvz., tai yra gera knyga.", ["Pvz."]),
        ("Prof. A. Jonaitis dirba universitete.", ["Prof.", "A."]),
        ("Tai kainavo 5 tūkst. eurų, t. y. labai daug.", ["tūkst.", "t.", "y."]),
        ("Knygos, sąsiuviniai ir t.t.", ["t.t."]),
        ("JIS GIMĖ 1990 M. KAUNE.", ["M."]),
    ],
)
def test_abbreviations_stay_whole_and_are_never_content(lithuanian, line, abbreviations):
    tokens = lithuanian(line)
    by_surface = {token.surface: token for token in tokens}
    for surface in abbreviations:
        assert by_surface[surface].feature.pos1 == "X", surface
    assert "".join(token.surface for token in tokens) == line.replace(" ", "")


def test_a_sentence_final_word_keeps_its_own_dot(lithuanian):
    assert [token.surface for token in lithuanian("Aš tave myliu.")][-2:] == ["myliu", "."]


def test_an_all_caps_cue_tags_from_the_lowercased_copy(lithuanian):
    tokens = _by_surface(lithuanian, "KAS ČIA VYKSTA?")
    assert (tokens["VYKSTA"].feature.pos1, tokens["VYKSTA"].feature.lemma) == ("VERB", "vykti")


def test_a_hyphen_between_names_splits(lithuanian):
    assert [token.surface for token in lithuanian("Vilniaus-Kauno kelias")][:3] == ["Vilniaus", "-", "Kauno"]


def test_the_fine_tag_and_morph_are_live(lithuanian):
    knyga = _by_surface(lithuanian, "Studentas vakar perskaitė įdomią knygą.")["knygą"]
    assert (knyga.feature.pos1, knyga.feature.pos2, knyga.feature.lemma) == ("NOUN", "dkt.mot.vns.G.", "knyga")
    assert knyga.morph == "Case=Acc|Gender=Fem|Number=Sing"


def test_dual_lives_on_pronouns_and_habitual_is_a_verb_feature(lithuanian):
    """E.2.7: no noun_plural (dual is pronominal) and no aspect_pair (Hab is not a Slavic pair)."""
    abi = _by_surface(lithuanian, "Abi seserys skaito knygas.")["Abi"]
    assert abi.feature.pos1 == "PRON" and "Number=Dual" in abi.morph
    verb = _by_surface(lithuanian, "Vaikystėje dažnai skaitydavau knygas.")["skaitydavau"]
    assert verb.feature.lemma == "skaityti" and "Aspect=Hab" in verb.morph


def test_a_capitalised_inflected_content_word_is_relemmatised(lithuanian):
    """Ruling S2 variant R: every subtitle cue starts capitalised, so this is roughly one word per cue."""
    tokens = _by_surface(lithuanian, "Vaikystėje dažnai skaitydavau knygas.")
    assert (tokens["Vaikystėje"].feature.pos1, tokens["Vaikystėje"].feature.lemma) == ("NOUN", "vaikystė")


def test_a_capitalised_name_is_left_alone(lithuanian):
    """R keeps POS and touches only a token whose raw lemma is its own surface: names stay out of mining.

    ``Kaunas`` is NOT the example: the model files it NOUN/``kaunas`` on its own, before any re-lemmatisation
    (16 of 45 gold sentence-initial names are already mistagged, unchanged by R - status 003-MEASURED).
    """
    tokens = _by_surface(lithuanian, "Vilnius yra Lietuvos sostinė.")
    assert tokens["Vilnius"].feature.pos1 == "PROPN"

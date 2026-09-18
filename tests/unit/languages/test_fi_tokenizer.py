"""The Finnish tokenizer over the real model: pipeline, surfaces, apostrophes, abbreviations, compounds, all-caps."""

from __future__ import annotations

import pytest

from anki_miner.languages.fi import tokenizer
from anki_miner.services.tagger import LockedTagger


@pytest.fixture(scope="module")
def finnish():
    return tokenizer.build_tagger()


def _by_surface(tagger, line):
    return {token.surface: token for token in tagger(line)}


def test_a_locked_tagger_without_the_parser(finnish):
    assert isinstance(finnish, LockedTagger)
    assert finnish.nlp.pipe_names == ["tok2vec", "tagger", "morphologizer", "lemmatizer", "attribute_ruler"]
    assert type(finnish.nlp.get_pipe("lemmatizer")).__name__ == "EditTreeLemmatizer"


@pytest.mark.parametrize(
    "line",
    [
        "Kirjassa oli kuvia.",
        "Söin ruo’an jälkeen jäätelöä.",
        "TÄMÄ ON LOPPU.",
        "Otin linja-auton keskustaan.",
        "Ostin mm. maitoa ja leipää.",
    ],
)
def test_surfaces_cover_the_line(finnish, line):
    assert "".join(token.surface for token in finnish(line)) == line.replace(" ", "")


def test_an_inessive_noun_lemmatises_with_a_live_fine_tag_and_its_case(finnish):
    tokens = _by_surface(finnish, "Kirjassa oli kuvia.")
    kirjassa = tokens["Kirjassa"]
    assert (kirjassa.feature.pos1, kirjassa.feature.pos2, kirjassa.feature.lemma) == ("NOUN", "N", "kirja")
    assert "Case=Ine" in kirjassa.morph
    assert (tokens["oli"].feature.pos1, tokens["oli"].feature.lemma) == ("AUX", "olla")


def test_the_gradation_apostrophe_stays_inside_one_token(finnish):
    tokens = finnish("Söin ruo’an jälkeen jäätelöä.")
    assert [token.surface for token in tokens] == ["Söin", "ruo’an", "jälkeen", "jäätelöä", "."]
    assert tokens[1].feature.pos1 == "NOUN"


def test_a_dotted_abbreviation_is_other_and_a_hyphen_compound_is_one_token(finnish):
    assert _by_surface(finnish, "Ostin mm. maitoa ja leipää.")["mm."].feature.pos1 == "X"
    assert _by_surface(finnish, "Otin linja-auton keskustaan.")["linja-auton"].feature.pos1 == "NOUN"


def test_an_all_caps_cue_is_tagged_from_the_lowercased_copy(finnish):
    tokens = _by_surface(finnish, "TÄMÄ ON LOPPU.")
    assert tokens["LOPPU"].feature.lemma == "loppu" and tokens["ON"].feature.lemma == "olla"


def test_a_clitic_the_model_drops_is_still_named_in_morph(finnish):
    kirjakin = _by_surface(finnish, "Hänellä oli kirjakin mukana.")["kirjakin"]
    assert kirjakin.feature.lemma == "kirja" and "Clitic=Kin" in kirjakin.morph


def test_a_capitalised_word_the_model_leaves_unlemmatised_is_relemmatised_in_lowercase(finnish):
    """RULING S2 variant R (F5): UD Finnish TDT dev+test content lemma exact 75.86 % -> 76.65 %, PROPN unchanged.

    ``Kirjakin`` is out of reach: the model already returns the lowercase lemma ``kirjakin``, so the repair's raw
    ``lemma_ == text`` gate never sees it (probed 2026-09-18; the plan's Task 10 contingency).
    """
    unohdin = _by_surface(finnish, "Unohdin kirjani kotiin.")["Unohdin"]
    assert (unohdin.feature.pos1, unohdin.feature.lemma) == ("VERB", "unohtaa")
    matti = _by_surface(finnish, "Matti, tule tänne!")["Matti"]
    assert (matti.feature.pos1, matti.feature.lemma) == ("PROPN", "Matti")  # POS is never changed

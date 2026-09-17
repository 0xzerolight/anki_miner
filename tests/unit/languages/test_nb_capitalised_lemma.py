"""RULING S2 FINAL opt-in: a capitalised content word whose lemma is its surface is re-lemmatised (B24)."""

from __future__ import annotations

import pytest

from anki_miner.languages.nb.tokenizer import build_tagger


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


@pytest.mark.parametrize(
    ("sentence", "surface", "lemma"),
    [
        ("Studenten leste en interessant bok i går.", "Studenten", "student"),
        ("Øynene hennes er blå.", "Øynene", "øye"),
    ],
)
def test_a_sentence_initial_noun_gets_its_dictionary_form(tagger, sentence, surface, lemma):
    (token,) = [t for t in tagger(sentence) if t.surface == surface]
    assert (token.feature.lemma, token.feature.pos1) == (lemma, "NOUN")


def test_a_capitalised_word_whose_lemma_is_already_lowercased_is_out_of_reach(tagger):
    """Variant R fires only on a RAW ``lemma_ == text``, so a wrong lowercase lemma is indistinguishable
    from a correct one and stays (``jenta``, not ``jente``). A residual miss, pinned here and in nb06."""
    (token,) = [t for t in tagger("Jenta leste boka.") if t.surface == "Jenta"]
    assert (token.feature.lemma, token.feature.pos1) == ("jenta", "NOUN")


def test_a_name_in_a_mixed_case_cue_is_left_alone(tagger):
    """A mixed-case cue is not an all-caps cue, so HUN keeps the tagger's own PROPN reading."""
    tokens = {t.surface: t.feature for t in tagger("De bor i Oslo. HUN ER IKKE HJEMME.")}
    assert (tokens["Oslo"].lemma, tokens["Oslo"].pos1) == ("Oslo", "PROPN")
    assert tokens["HUN"].lemma == "HUN"


def test_an_all_caps_cue_is_lowered_by_the_existing_path(tagger):
    """The all-caps cue path (not variant R) is what repairs a shouted line."""
    tokens = {t.surface: t.feature for t in tagger("HUN ER IKKE HJEMME.")}
    assert (tokens["HUN"].lemma, tokens["HUN"].pos1) == ("hun", "PRON")

"""RULING S2 FINAL opt-in: a capitalised Polish content word whose lemma is its surface is re-lemmatised."""

from __future__ import annotations

import pytest

from anki_miner.languages.pl.tokenizer import build_tagger


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


@pytest.mark.parametrize(
    ("sentence", "surface", "lemma"),
    [
        ("Boję się ciemności.", "Boję", "bać"),
        ("Kupiliśmy owoce, np. jabłka i gruszki.", "Kupiliśmy", "kupić"),
        ("Otwórz książkę na str. 12.", "Otwórz", "otworzyć"),
        ("Ćwiczę codziennie rano.", "Ćwiczę", "ćwiczyć"),
    ],
)
def test_a_sentence_initial_content_word_gets_its_dictionary_form(tagger, sentence, surface, lemma):
    """Probed 2026-09-18: these four are the corpus's real capitalised-identity misses (pl07 pl17 pl18 pl29).

    Książki, Łódź, Ślub and Źle already lemmatise correctly WITHOUT variant R, so they prove nothing.
    """
    (token,) = [t for t in tagger(sentence) if t.surface == surface]
    assert token.feature.lemma == lemma


def test_a_name_keeps_its_own_lemma(tagger):
    """(d) held at 234: a PROPN is outside CAPITALISED_LEMMA_POS, so Łukasz stays a name."""
    tokens = {t.surface: t.feature for t in tagger("Łukasz mieszka w Warszawie.")}
    assert tokens["Łukasz"].pos1 == "PROPN" and tokens["Warszawie"].pos1 == "PROPN"
    assert tokens["Łukasz"].lemma == "Łukasz"


def test_the_residual_miss_is_a_lowercased_identity(tagger):
    """Variant R lowercases and re-lemmatises, but the lemmatiser returns the same word: ``Mógłbyś`` →
    ``mógłbyś``, a lowercase non-dictionary front. The residual miss of P18, pinned (pl10)."""
    (token,) = [t for t in tagger("Mógłbyś mi pomóc?") if t.surface == "Mógłbyś"]
    assert token.feature.lemma == "mógłbyś"


def test_an_all_caps_cue_is_lowered_by_the_existing_path(tagger):
    """The all-caps cue path (not variant R) is what repairs a shouted line."""
    tokens = {t.surface: t.feature for t in tagger("KONIEC FILMU")}
    assert tokens["KONIEC"].lemma == "koniec"

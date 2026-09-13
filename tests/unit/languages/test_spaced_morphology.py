"""Casing post-pass, tagging copy, mined-form policy and the Latin lookup ladder (stub data, no engine)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.languages._spaced.morphology import (
    APOSTROPHE_FOLD,
    EncliticRung,
    LatinLookupStrategy,
    SpacedMinedForm,
    case_lemma,
    is_all_caps_cue,
    tagging_copy,
)


@pytest.mark.parametrize(
    ("lemma", "pos", "title", "expected"),
    [
        ("Went", "VERB", frozenset(), "went"),
        ("London", "PROPN", frozenset(), "London"),
        ("haus", "NOUN", frozenset({"NOUN"}), "Haus"),
        ("Haus", "NOUN", frozenset({"NOUN"}), "Haus"),
        ("GROSS", "ADJ", frozenset({"NOUN"}), "gross"),
    ],
)
def test_case_lemma(lemma, pos, title, expected):
    assert case_lemma(lemma, pos, title) == expected


@pytest.mark.parametrize(
    ("text", "caps"),
    [
        ("THE END", True),
        ("I DON'T KNOW", True),
        ("ICH WEIß ES NICHT", True),  # ß has no one-character capital (de A7)
        ("OK", False),
        ("The End", False),
        ("NASA is here", False),
        ("42 !", False),
        ("ß ß", False),  # no uppercase at all
    ],
)
def test_all_caps_cue(text, caps):
    assert is_all_caps_cue(text) is caps


def test_a_shouted_german_cue_lowercases_and_keeps_its_length():
    assert tagging_copy("ICH WEIß ES NICHT") == "ich weiß es nicht"


def test_tagging_copy_keeps_the_length_and_folds_apostrophes_and_shouting():
    assert tagging_copy("I DON’T KNOW", APOSTROPHE_FOLD) == "i don't know"
    assert tagging_copy("She can’t.", APOSTROPHE_FOLD) == "She can't."
    assert tagging_copy("İSTANBUL WAS BIG", {}) == "İSTANBUL WAS BIG"  # lowercasing İ grows the text: skipped
    for text in ("THE END", "don’t", "Plain text"):
        assert len(tagging_copy(text, APOSTROPHE_FOLD)) == len(text)


def test_apostrophe_map_is_one_char_to_one_char():
    assert all(len(k) == 1 and len(v) == 1 for k, v in APOSTROPHE_FOLD.items())


def test_mined_form_is_the_lemma_and_never_tracks_the_surface():
    policy = SpacedMinedForm()
    assert policy.mined_form("VERB", "go", "go", "went") == "go"
    assert policy.mined_form("NOUN", "", "", "dogs") == "dogs"
    assert policy.expression_tracks_surface(SimpleNamespace(pos="VERB")) is False


def test_the_policy_hands_the_ladder_the_surface():
    assert SpacedMinedForm().lookup_alternate(SimpleNamespace(surface="Dámelo", mined_form="dámelir")) == "Dámelo"
    assert SpacedMinedForm().lookup_alternate(SimpleNamespace()) == ""


def test_ladder_order_surface_rungs_then_extras_then_hyphen_parts():
    seen: list[tuple[str, str]] = []

    def rung(word: str, surface: str) -> list[str]:
        seen.append((word, surface))
        return ["x-rung", word]

    ladder = LatinLookupStrategy(extra_rungs=(rung,))
    assert ladder.candidates("well-known", "Well-Known", None) == [
        ("Well-Known", 0),
        ("x-rung", 0),
        ("well", 0),
        ("known", 0),
    ]
    assert seen == [("well-known", "Well-Known")]


def test_the_casefolded_surface_follows_the_verbatim_one():
    assert LatinLookupStrategy().candidates("go", "WENT", None) == [("WENT", 0), ("went", 0)]


def test_ladder_never_returns_the_probe_word_or_duplicates():
    assert LatinLookupStrategy().candidates("went", "", None) == []
    assert LatinLookupStrategy().candidates("went", "went", None) == []
    result = LatinLookupStrategy(extra_rungs=(lambda w, s: ["go", "go"],)).candidates("went", "Went", None)
    assert result == [("Went", 0), ("go", 0)]


def test_enclitic_rung_strips_the_surface_longest_first_and_relemmatizes():
    rung = EncliticRung(clusters=("lo", "melo"), relemmatize=lambda stem: [stem.replace("á", "a") + "r"])
    assert rung("dámelir", "Dámelo") == ["dá", "dar", "dáme", "damer"]
    assert rung("lo", "lo") == []  # no stem of two characters left
    assert EncliticRung(clusters=("se",))("levantarse", "") == ["levantar"]  # no surface: the mined form


def test_an_enclitic_rung_plugs_into_the_ladder_on_the_surface():
    ladder = LatinLookupStrategy(extra_rungs=(EncliticRung(clusters=("melo",)),))
    assert ladder.candidates("dámelir", "Dámelo", None) == [("Dámelo", 0), ("dámelo", 0), ("dá", 0)]

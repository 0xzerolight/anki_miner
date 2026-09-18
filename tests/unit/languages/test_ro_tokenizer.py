"""The Romanian tokenizer: cedilla copy, main verbs, the capitalised-lemma opt-in, clitics (real model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages._spaced.morphology import CAPITALISED_LEMMA_POS
from anki_miner.languages._spaced.tokenizer import _splits_letter_hyphen_letter, load_spacy_model
from anki_miner.languages.ro.morphology import RO_MODEL_PACKAGE
from anki_miner.languages.ro.tokenizer import RO_RELEMMATISE_POS, build_tagger
from anki_miner.services.tagger import LockedTagger

PIPELINE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "ro" / "model_pipeline.json").read_text(encoding="utf-8")
)
CONTENT = {"NOUN", "VERB", "ADJ", "ADV"}
CEDILLA_STIINTA = "\u015etiin\u0163a"  # \u015etiin\u0163a
CEDILLA_THANKS = "Mul\u0163umesc"  # Mul\u0163umesc


def test_the_relemmatise_set_is_the_shared_one_plus_aux():
    """R1: ro's attribute ruler files main verbs as AUX, and the repair runs before ``main_verb_pos``."""
    assert CAPITALISED_LEMMA_POS | {"AUX"} == RO_RELEMMATISE_POS


@pytest.fixture(scope="module")
def romanian():
    return build_tagger()


@pytest.fixture(scope="module")
def raw():
    return load_spacy_model(RO_MODEL_PACKAGE, keep_parser=False)


def test_the_raw_model_matches_the_recorded_pipeline(raw):
    nlp = raw
    assert nlp.pipe_names == PIPELINE["pipeline"] and "morphologizer" not in nlp.pipe_names
    assert type(nlp.get_pipe("lemmatizer")).__name__ == PIPELINE["lemmatizer"]
    doc = nlp(PIPELINE["sentence"])
    observed = [{"text": t.text, "pos": t.pos_, "tag": t.tag_, "lemma": t.lemma_, "morph": str(t.morph)} for t in doc]
    assert observed == PIPELINE["tokens"]
    first = doc[0]
    assert (first.text, first.lemma_) == ("Cărțile", "Cărțile") and str(first.morph)  # R1, E.2.5
    assert {(t.text, t.pos_, t.tag_) for t in doc} >= {("știu", "AUX", "Vmip1s")}  # R2


def test_build_tagger_is_locked_and_repairs_the_recorded_sentence(romanian):
    assert isinstance(romanian, LockedTagger)
    assert romanian.nlp.pipe_names == PIPELINE["pipeline"]
    tokens = {t.surface: t for t in romanian(PIPELINE["sentence"])}
    assert (tokens["Cărțile"].feature.pos1, tokens["Cărțile"].feature.lemma) == ("NOUN", "carte")
    assert tokens["Cărțile"].morph == "Case=Acc,Nom|Definite=Def|Gender=Fem|Number=Plur"
    assert (tokens["știu"].feature.pos1, tokens["știu"].feature.lemma) == ("VERB", "ști")
    assert tokens["știu"].feature.pos2 == "Vmip1s"  # pos2 is live: a trained tagger


@pytest.mark.parametrize(
    ("text", "surface", "lemma"),
    [
        ("Cărțile sunt pe masă.", "Cărțile", "carte"),
        ("Mergem acasă.", "Mergem", "merge"),
        ("Vreau să mănânc ceva.", "Vreau", "vrea"),  # tagged AUX before main_verb_pos: the AUX case
        ("Întors acasă, a dormit.", "Întors", "întoarce"),
    ],
)
def test_a_capitalised_word_the_model_echoes_is_lemmatised_from_the_lowercased_word(
    raw, romanian, text, surface, lemma
):
    assert {t.text: t.lemma_ for t in raw(text)}[surface] == surface  # the raw model echoes it: a repair case
    by_surface = {t.surface: t.feature for t in romanian(text)}
    assert by_surface[surface].lemma == lemma


@pytest.mark.parametrize(
    ("text", "surface", "lemma"),
    [
        ("Copiii se joacă în parc.", "Copiii", "copil"),
        ("Fata mea citește o carte.", "Fata", "fată"),
        ("Scaunele sunt noi.", "Scaunele", "scaun"),
    ],
)
def test_a_capitalised_word_the_model_already_lemmatised_is_left_alone(raw, romanian, text, surface, lemma):
    """The repair qualifies on the raw lemma, so a word the model read correctly never re-enters the model."""
    assert {t.text: t.lemma_ for t in raw(text)}[surface] == lemma
    by_surface = {t.surface: t.feature for t in romanian(text)}
    assert by_surface[surface].lemma == lemma


def test_the_repair_equals_the_model_reading_the_word_alone(raw, romanian):
    expected = raw("cărțile")[0].lemma_
    assert romanian("Cărțile sunt pe masă.")[0].feature.lemma == expected == "carte"


def test_a_name_and_an_all_caps_cue_are_left_to_the_shared_passes(romanian):
    maria = romanian("Maria citește.")[0]
    assert (maria.surface, maria.feature.pos1, maria.feature.lemma) == ("Maria", "PROPN", "Maria")
    cue = {t.surface: t.feature.lemma for t in romanian("SFÂRȘITUL POVEȘTII")}
    assert cue["SFÂRȘITUL"] == "sfârșit"  # tagging_copy lowered the line


@pytest.mark.parametrize(
    ("text", "surface", "lemma", "pos1"),
    [
        ("Nu știu ce să fac.", "știu", "ști", "VERB"),
        ("Nu știu ce să fac.", "fac", "face", "VERB"),
        ("Mi-a spus că vine mâine.", "vine", "veni", "VERB"),
        ("Casa lor e frumoasă.", "e", "fi", "AUX"),  # Vaip3s stays an auxiliary
    ],
)
def test_main_verbs_are_verbs_and_auxiliaries_stay(romanian, text, surface, lemma, pos1):
    by_surface = {t.surface: t.feature for t in romanian(text)}
    assert (by_surface[surface].lemma, by_surface[surface].pos1) == (lemma, pos1)


@pytest.mark.parametrize(
    ("text", "surface", "lemma", "pos1"),
    [
        (CEDILLA_STIINTA + " e grea.", CEDILLA_STIINTA, "știință", "NOUN"),
        (CEDILLA_THANKS + " foarte mult!", CEDILLA_THANKS, "mulțumi", "VERB"),
    ],
)
def test_a_cedilla_line_is_tagged_from_the_comma_below_copy_with_its_surface_verbatim(
    romanian, text, surface, lemma, pos1
):
    by_surface = {t.surface: t.feature for t in romanian(text)}
    assert (by_surface[surface].lemma, by_surface[surface].pos1) == (lemma, pos1)


@pytest.mark.parametrize(
    ("text", "surfaces", "content"),
    [
        (
            "S-a dus acasă într-un autobuz.",
            ["S-", "a", "dus", "acasă", "într-", "un", "autobuz", "."],
            {"dus", "acasă", "autobuz"},
        ),
        ("Mi-a spus.", ["Mi-", "a", "spus", "."], {"spus"}),
        ("Dă-mi cartea!", ["Dă", "-mi", "cartea", "!"], {"Dă", "cartea"}),
        ("L-am văzut.", ["L-", "am", "văzut", "."], {"văzut"}),
    ],
)
def test_hyphen_clitics_split_at_the_tokenizer(romanian, text, surfaces, content):
    """E.2.5: no hyphen rung exists for ro because nothing joined is left to strip."""
    tokens = romanian(text)
    assert [t.surface for t in tokens] == surfaces
    assert {t.surface for t in tokens if t.feature.pos1 in CONTENT} == content


def test_the_clitic_split_is_spacy_romanian_prefix_stripping_not_an_infix():
    from spacy.lang.ro import Romanian
    from spacy.lang.ro.punctuation import _ud_rrt_prefixes

    assert {"s-", "într-", "mi-", "l-", "n-"} <= set(_ud_rrt_prefixes)
    assert not any(_splits_letter_hyphen_letter(pattern) for pattern in Romanian.Defaults.infixes)


@pytest.mark.parametrize(
    ("text", "surfaces"),
    [
        (
            "Am cumpărat pere ș.a.m.d. Apoi am plecat.",
            ["Am", "cumpărat", "pere", "ș.a.m.d.", "Apoi", "am", "plecat", "."],
        ),
        ("Dr. Ionescu a venit.", ["Dr.", "Ionescu", "a", "venit", "."]),
        ("Vreau rom.", ["Vreau", "rom", "."]),  # the rom. exception is pruned: rom is not an abbreviation here
        ("L-am văzut pe Ian.", ["L-", "am", "văzut", "pe", "Ian", "."]),
    ],
)
def test_the_abbreviation_set_shapes_dotted_tokens(romanian, text, surfaces):
    tokens = romanian(text)
    assert [t.surface for t in tokens] == surfaces
    assert all(t.feature.pos1 == "X" for t in tokens if t.surface in {"ș.a.m.d.", "Dr."})

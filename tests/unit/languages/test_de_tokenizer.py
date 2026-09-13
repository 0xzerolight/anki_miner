"""The German tokenizer over the real model: surfaces, clitics, abbreviations, casing, ADJD and the svp stash."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.de import tokenizer
from anki_miner.services.tagger import LockedTagger


@pytest.fixture(scope="module")
def german():
    return tokenizer.build_tagger()


def _by_surface(tagger, line):
    return {token.surface: token for token in tagger(line)}


def test_a_locked_tagger_over_the_parser_pipeline(german):
    assert isinstance(german, LockedTagger)
    assert german.nlp.pipe_names == ["tok2vec", "tagger", "morphologizer", "parser", "lemmatizer", "attribute_ruler"]


@pytest.mark.parametrize(
    "line",
    [
        "Er sieht sich den Film an.",
        "Wie geht’s dir?",
        "DER HUND BELLT DIE GANZE NACHT.",
        "Schick mir eine E-Mail, z.B. heute.",
    ],
)
def test_surfaces_cover_the_line(german, line):
    assert "".join(token.surface for token in german(line)) == line.replace(" ", "")


def test_the_particle_is_stashed_on_its_verb(german):
    tokens = _by_surface(german, "Er sieht sich den Film an.")
    assert tokens["sieht"].feature.lemma == "sehen" and tokens["sieht"].feature.particle == "an"
    assert tokens["an"].feature.pos1 == "PART"
    assert (tokens["Film"].feature.lemma, tokens["Film"].feature.pos2) == ("Film", "NN")
    assert "Gender=Masc" in tokens["Film"].morph  # morph is a LanguageToken slot, not a feature field


def test_a_misparsed_head_stashes_nothing(german):
    tokens = _by_surface(german, "Mach bitte die Tür zu.")
    assert not getattr(tokens["Mach"].feature, "particle", "")  # the model reads Mach as PROPN
    assert tokens["zu"].feature.pos2 == "PTKVZ"


@pytest.mark.parametrize(("line", "clitic"), [("Wie geht's dir?", "'s"), ("Wie geht’s dir?", "’s")])
def test_the_es_clitic_splits_off_the_verb(german, line, clitic):
    tokens = german(line)
    assert [token.surface for token in tokens][1:3] == ["geht", clitic]
    assert (tokens[1].feature.pos1, tokens[1].feature.lemma) == ("VERB", "gehen")
    assert tokens[2].feature.pos1 == "PRON"


@pytest.mark.parametrize(
    ("line", "abbreviation"),
    [
        ("Wir treffen uns z.B. am Bahnhof.", "z.B."),
        ("Dr. Weber kommt heute.", "Dr."),
        ("Brot, Milch usw. für heute.", "usw."),
    ],
)
def test_dotted_abbreviations_are_never_vocabulary(german, line, abbreviation):
    assert _by_surface(german, line)[abbreviation].feature.pos1 == "X"


@pytest.mark.parametrize(("line", "word"), [("Das ist Jan.", "Jan"), ("Wir sehen uns am So.", "So")])
def test_a_sentence_final_word_keeps_its_own_token(german, line, word):
    surfaces = [token.surface for token in german(line)]
    assert surfaces[-2:] == [word, "."]


def test_predicative_adjectives_are_adjectives(german):
    tokens = _by_surface(german, "Der Kühlschrank ist schon wieder leer.")
    assert (tokens["leer"].feature.pos1, tokens["leer"].feature.pos2) == ("ADJ", "ADJD")
    assert tokens["schon"].feature.pos1 == "ADV"


def test_hyphen_compounds_are_one_token_and_the_infix_removal_is_refused(german):
    assert "U-Bahn" in [token.surface for token in german("Wir nehmen die U-Bahn.")]
    with pytest.raises(ValueError, match="hyphen"):
        build_spacy_tagger("de_core_news_sm", join_hyphenated=True)


def test_a_shouted_cue_capitalises_its_nouns(german):
    tokens = _by_surface(german, "DER HUND BELLT DIE GANZE NACHT.")
    assert (tokens["HUND"].feature.pos1, tokens["HUND"].feature.lemma) == ("NOUN", "Hund")
    assert (tokens["BELLT"].feature.pos1, tokens["BELLT"].feature.lemma) == ("VERB", "bellen")
    assert tokens["NACHT"].feature.lemma == "Nacht"


def test_sharp_s_in_a_shouted_cue(german):
    tokens = _by_surface(german, "ICH WEIß ES NICHT.")
    assert (tokens["WEIß"].feature.pos1, tokens["WEIß"].feature.lemma) == ("VERB", "wissen")

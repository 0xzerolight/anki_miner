"""The Catalan tokenizer: lemma correction, tokenizer-level clitic splits, curly apostrophes (real model)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.languages._spaced.tokenizer import load_spacy_model
from anki_miner.languages.ca.morphology import CA_MODEL_PACKAGE, install_lemma_correction
from anki_miner.languages.ca.tokenizer import build_tagger
from anki_miner.services.tagger import LockedTagger

PIPELINE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "ca" / "model_pipeline.json").read_text(encoding="utf-8")
)
CONTENT = {"NOUN", "VERB", "ADJ", "ADV"}


# --- the correction against a stub lemmatizer (no model) -------------------------------------------------

RULE_FORMS = {
    "llibres": ["llibra", "llibre"],  # two in-index rule candidates, spaCy keeps the first
    "juguen": ["juguar"],  # out-of-index guess; the lookup answer is an indexed verb
    "pomes": ["pomer"],  # out-of-index guess; the lookup answer is not an indexed adjective
    "gats": ["gat"],  # indexed, lookup agrees
    "presidenta": ["president"],  # indexed; the lookup disagrees -> the rule stands
    "vols": ["vol"],  # no lookup entry
}
TABLES = {
    "lemma_lookup": {
        "llibres": ["llibre"],
        "juguen": ["jugar"],
        "pomes": ["poma"],
        "gats": ["gat"],
        "presidenta": ["presidenta"],
    },
    "lemma_index": {
        "noun": ["llibra", "llibre", "gat", "president", "presidenta", "poma"],
        "verb": ["jugar"],
        "adj": ["pom"],
    },
}


def _stub_nlp():
    lemmatizer = SimpleNamespace(
        lookups=SimpleNamespace(get_table=TABLES.__getitem__),
        lemmatize=lambda token: RULE_FORMS[token.text.lower()],
    )
    return SimpleNamespace(get_pipe={"lemmatizer": lemmatizer}.__getitem__), lemmatizer


@pytest.mark.parametrize(
    ("text", "pos", "lemma"),
    [
        ("llibres", "NOUN", "llibre"),
        ("Juguen", "VERB", "jugar"),
        ("pomes", "ADJ", "pomer"),
        ("gats", "NOUN", "gat"),
        ("presidenta", "NOUN", "president"),
        ("vols", "NOUN", "vol"),
    ],
)
def test_the_lookup_answer_wins_only_when_the_rules_allow_it(text, pos, lemma):
    nlp, lemmatizer = _stub_nlp()
    install_lemma_correction(nlp)
    assert lemmatizer.lemmatize(SimpleNamespace(text=text, pos_=pos))[0] == lemma


# --- the real model -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalan():
    return build_tagger()


def test_the_raw_model_matches_the_recorded_pipeline():
    nlp = load_spacy_model(CA_MODEL_PACKAGE, keep_parser=False)
    assert nlp.pipe_names == PIPELINE["pipeline"]
    assert nlp.get_pipe("lemmatizer").mode == PIPELINE["lemmatizer_mode"]
    doc = nlp(PIPELINE["sentence"])
    observed = [{"text": t.text, "pos": t.pos_, "tag": t.tag_, "lemma": t.lemma_, "morph": str(t.morph)} for t in doc]
    assert observed == PIPELINE["tokens"]
    assert all(t.tag_ == t.pos_ for t in doc)  # no trained tagger: pos2 is dead


def test_build_tagger_is_locked_and_corrects_the_probe_word(catalan):
    assert isinstance(catalan, LockedTagger)
    assert catalan.nlp.pipe_names == PIPELINE["pipeline"]
    tokens = {t.surface: t for t in catalan(PIPELINE["sentence"])}
    assert (tokens["llibres"].feature.pos1, tokens["llibres"].feature.lemma) == ("NOUN", "llibre")
    assert tokens["llibres"].morph == "Gender=Masc|Number=Plur"  # a LanguageToken slot (Stage S core)
    assert all(t.feature.pos2 == "" for t in tokens.values())


@pytest.mark.parametrize(
    ("text", "surfaces"),
    [
        ("Ho sé—potser no.", ["Ho", "sé", "—", "potser", "no", "."]),  # en's dash infix; the raw model keeps sé—potser
        ("Un amic hispano-americà.", ["Un", "amic", "hispano-americà", "."]),  # join_hyphenated=False: stock tokenizer
        ("Tinc set.", ["Tinc", "set", "."]),  # the set. (setembre) exception is pruned: set is not in CA_ABBREVIATIONS
        ("El Sr. Puig.", ["El", "Sr.", "Puig", "."]),  # Sr is: the exception stays, and the token is a dotted X
    ],
)
def test_the_shared_adapter_shapes_catalan_tokens(catalan, text, surfaces):
    tokens = catalan(text)
    assert [t.surface for t in tokens] == surfaces
    by_surface = {t.surface: t.feature.pos1 for t in tokens}
    assert by_surface.get("Sr.", "X") == "X"
    assert by_surface.get("set", "NUM") not in {"PUNCT", "X"}


@pytest.mark.parametrize(
    ("text", "lemma"),
    [
        ("juguen", "jugar"),
        ("vingui", "venir"),
        ("sé", "saber"),
        ("veiem", "veure"),
        ("sabia", "saber"),
        ("mengem", "menjar"),
    ],
)
def test_irregular_verbs_reach_their_infinitive(catalan, text, lemma):
    by_surface = {t.surface: t.feature.lemma for t in catalan(f"Ells {text} ara.")}
    assert by_surface[text] == lemma


@pytest.mark.parametrize(
    ("text", "surfaces", "content"),
    [
        ("donar-me'l", ["donar", "-me", "'l"], {"donar"}),
        ("anem-hi", ["anem", "-hi"], {"anem"}),
        ("l'home", ["l'", "home"], {"home"}),
        ("donar-me’l", ["donar", "-me", "’l"], {"donar"}),
        ("del", ["d", "el"], set()),
    ],
)
def test_clitics_and_articles_split_at_the_tokenizer(catalan, text, surfaces, content):
    """E.2.5: no elision or enclitic rung exists for ca because nothing joined is left to strip."""
    tokens = catalan(text)
    assert [t.surface for t in tokens] == surfaces
    assert {t.surface for t in tokens if t.feature.pos1 in CONTENT} == content


def test_the_pronom_feble_infix_is_spacy_catalan_evidence():
    from spacy.lang.ca.punctuation import TOKENIZER_INFIXES

    infix = next(rule for rule in TOKENIZER_INFIXES if "-hi" in rule)
    clitics = ("'ls", "'l", "'ns", "'t", "'m", "'n", "-les", "-la", "-lo", "-li", "-los", "-me")
    clitics += ("-nos", "-te", "-vos", "-se", "-hi", "-ne", "-ho", "-l'", "-m'", "-t'", "-n'")
    for clitic in clitics:
        assert clitic in infix


def test_curly_apostrophes_tag_from_the_folded_copy(catalan):
    features = {t.surface: t.feature for t in catalan("L’home vol donar-me’l.")}
    assert features["L’"].pos1 == "DET"
    assert features["’l"].pos1 == "PRON"
    assert features["home"].pos1 == "NOUN"


def test_the_interpunct_word_is_one_noun(catalan):
    features = {t.surface: t.feature for t in catalan("Els col·legis nous.")}
    assert (features["col·legis"].pos1, features["col·legis"].lemma) == ("NOUN", "col·legi")

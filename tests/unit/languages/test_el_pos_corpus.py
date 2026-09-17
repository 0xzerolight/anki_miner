"""Greek mining over self-written sentences through the REAL parser and model (E.9 fixtures).

Records marked "documented miss" pin real el_core_news_sm behaviour the plan accepts; a model or
substrate change that fixes one fails here on purpose, so the fixture is updated with evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.el.morphology import EL_ALLOWED_POS
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "el"


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines() if line.strip()]


RECORDS = _load("pos_corpus.jsonl")
CAPS = _load("casing_all_caps.jsonl")


# Module scope: conftest's per-test tagger-cache reset would otherwise reload el_core_news_sm per record.
@pytest.fixture(scope="module")
def parser():
    return get_profile("el").create_parser(switch_language(AnkiMinerConfig(), "el"))


@pytest.fixture(scope="module")
def tagger():
    return get_tagger("el")


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


def _normalized(sentence: str) -> str:
    return get_profile("el").normalize(sentence)


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words = _words(parser, record["sentence"])
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    assert not any(" " in front for front in mined), record["id"]  # the ADP lemma "σε ο" never reaches a front
    assert all(word.surface in _normalized(record["sentence"]) for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    line = _normalized(record["sentence"])
    assert "".join(token.surface for token in tagger(line)) == line.replace(" ", "")


def test_pos2_is_dead_on_every_token(tagger):
    """D11: no trained tagger, and the attribute ruler copies POS to TAG."""
    assert "tagger" not in tagger.nlp.pipe_names and tagger.nlp.get_pipe("attribute_ruler").labels == ()
    assert all(tok.tag_ == tok.pos_ for tok in tagger.nlp("Το βιβλίο είναι στο σπίτι."))
    for record in RECORDS + CAPS:
        assert all(token.feature.pos2 == "" for token in tagger(_normalized(record["sentence"]))), record["id"]


def test_the_contraction_lemma_holds_a_space_and_is_a_function_word(tagger):
    (sto,) = [token for token in tagger("Το βιβλίο είναι στο σπίτι.") if token.surface == "στο"]
    assert (sto.feature.pos1, sto.feature.lemma) == ("ADP", "σε ο")
    assert "ADP" not in EL_ALLOWED_POS


def test_a_modifier_apostrophe_elision_tags_as_a_function_word(tagger):
    (elided,) = [token for token in tagger("Θ\u02bc αγοράσω ένα βιβλίο.") if token.surface == "Θ\u02bc"]
    assert elided.feature.pos1 == "AUX"


def test_the_greek_question_mark_splits_only_after_normalize(tagger):
    assert [t.surface for t in tagger("Τι κάνεις\u037e")][-1] == "κάνεις\u037e"
    assert [t.surface for t in tagger(_normalized("Τι κάνεις\u037e"))][-2:] == ["κάνεις", ";"]


@pytest.mark.parametrize("record", CAPS, ids=[record["id"] for record in CAPS])
def test_an_all_caps_cue_is_a_settled_miss(tagger, parser, record):
    """R34: tagged from a lowercased copy; the tonos never returns, so the front misses the dictionary key."""
    assert [[t.surface, t.feature.pos1, t.feature.lemma] for t in tagger(record["sentence"])] == record["tokens"]
    words = _words(parser, record["sentence"])
    assert {word.mined_form for word in words} == set(record["must_mine"])
    assert all(record["sentence"][word.surface_start : word.surface_end] == word.surface for word in words)
    assert all(word.surface.isupper() for word in words)
    keys = get_profile("el").dict_keys
    for front, key in zip(record["must_mine"], record["dictionary_keys"], strict=True):
        assert keys.fold_term(front) != keys.fold_term(key)


def test_a_capitalised_inflected_content_word_is_relemmatised(tagger):
    tokens = {token.surface: token for token in tagger("Εκφράζουμε τη λύπη μας.")}
    assert (tokens["Εκφράζουμε"].feature.pos1, tokens["Εκφράζουμε"].feature.lemma) == ("VERB", "εκφράζω")


def test_a_function_word_tagged_capital_verb_is_not_touched(tagger):
    """R keeps POS: Κλείσε tagged PROPN stays out of mining (el16's class of miss)."""
    tokens = {token.surface: token for token in tagger("Κλείσε την πόρτα.")}
    assert tokens["Κλείσε"].feature.pos1 == "PROPN"

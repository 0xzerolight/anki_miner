"""Norwegian Bokmål mining over self-written sentences through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.nb.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "nb" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


@pytest.fixture(scope="module")
def parser():
    return get_profile("nb").create_parser(switch_language(AnkiMinerConfig(), "nb"))


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words = _words(parser, record["sentence"])
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    assert all(word.surface in record["sentence"] for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    tokens = tagger(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_pos2_is_dead_for_the_whole_corpus(tagger):
    """D11: no tagger component, so the fine tag is never more than UPOS and excluded_subtypes gates nothing."""
    assert {token.feature.pos2 for record in RECORDS for token in tagger(record["sentence"])} == {""}
    assert get_profile("nb").pos_defaults.excluded_subtypes == ()


@pytest.mark.parametrize(
    ("sentence", "surface", "morph"),
    [("Boka ligger på bordet.", "Boka", "Fem"), ("Boken ligger på bordet.", "Boken", "Masc")],
)
def test_both_definite_forms_reach_bok_with_their_own_gender(tagger, sentence, surface, morph):
    (token,) = [t for t in tagger(sentence) if t.surface == surface]
    assert token.feature.lemma == "bok" and f"Gender={morph}" in token.morph.split("|")


def test_the_probe_verb_lemmatises(parser):
    assert "gå hjem" in {word.mined_form for word in _words(parser, "Hun gikk hjem.")}
    fronts = {word.mined_form for word in _words(parser, "Hun gikk.")}
    assert "gå" in fronts


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    fronts = {word.mined_form: word for word in _words(parser, "BOKA LIGGER PÅ BORDET.")}
    assert {"bok", "ligge", "bord"} <= set(fronts)
    word = fronts["bok"]
    assert word.surface == "BOKA"
    assert "BOKA LIGGER PÅ BORDET."[word.surface_start : word.surface_end] == "BOKA"


def test_the_known_word_front_en_bok_meets_the_mined_bok(parser):
    fold = get_profile("nb").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "Studenten leste en bok.") if w.mined_form == "bok"]
    assert fold("en bok") == fold(word.mined_form)

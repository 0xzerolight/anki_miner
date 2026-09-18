"""Swedish mining over self-written sentences through the REAL parser and model (B.8 Stage D fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.sv.morphology import SV_ALLOWED_POS, SV_EXCLUDED_SUBTYPES
from anki_miner.languages.sv.tokenizer import build_tagger
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "sv" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every SUC tag sv_core_news_sm put under ADJ/ADV/NOUN/VERB over this corpus. A new one fails here.
EXPECTED_FINE_TAGS = {
    "AB", "AB|POS", "JJ|POS|NEU|SIN|IND|NOM", "JJ|POS|UTR/NEU|PLU|IND/DEF|NOM", "JJ|POS|UTR/NEU|SIN|DEF|NOM",
    "JJ|POS|UTR|SIN|IND|NOM", "NN|-|-|-|-", "NN|NEU|PLU|DEF|NOM", "NN|NEU|PLU|IND|NOM", "NN|NEU|SIN|DEF|GEN",
    "NN|NEU|SIN|DEF|NOM", "NN|NEU|SIN|IND|NOM", "NN|UTR|PLU|DEF|GEN", "NN|UTR|PLU|IND|NOM", "NN|UTR|SIN|DEF|GEN",
    "NN|UTR|SIN|DEF|NOM", "NN|UTR|SIN|IND|NOM", "PC|PRF|NEU|SIN|IND|NOM", "PL", "RO|NOM", "VB|IMP|AKT",
    "VB|INF|AKT", "VB|PRS|AKT", "VB|PRT|AKT", "VB|SUP|AKT",
}  # fmt: skip


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


@pytest.fixture(scope="module")
def parser():
    return get_profile("sv").create_parser(switch_language(AnkiMinerConfig(), "sv"))


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    mined = {word.mined_form for word in _words(parser, record["sentence"])}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    tokens = tagger(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_no_unexpected_fine_tag_appears_under_an_allowed_class(tagger):
    seen = {
        token.feature.pos2
        for record in RECORDS
        for token in tagger(record["sentence"])
        if token.feature.pos1 in SV_ALLOWED_POS and token.feature.pos2
    }
    assert seen == EXPECTED_FINE_TAGS


def test_the_excluded_subtypes_are_real_tagger_labels(tagger):
    assert set(SV_EXCLUDED_SUBTYPES) <= set(tagger.nlp.get_pipe("tagger").labels)


def test_the_recorded_model_misses_are_still_the_recorded_ones(parser):
    """Not aspirations: each is a miss this release ships, pinned so a change to it is visible."""
    assert "anna" in {word.mined_form for word in _words(parser, "Annas bok ligger på bordet.")}
    assert "slutet" in {word.mined_form for word in _words(parser, "Det är SLUTET nu.")}
    assert "igå" in {word.mined_form for word in _words(parser, "Jag såg honom igår.")}

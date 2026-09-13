"""Spanish mining over self-written sentences through the REAL parser and model (A.5 Stage 3 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.es.morphology import ES_ALLOWED_POS, ES_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "es" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def parser():
    return get_profile("es").create_parser(switch_language(AnkiMinerConfig(), "es"))


def _words(parser, sentence: str, **kwargs):
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], False, **kwargs
    )
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words = _words(parser, record["sentence"])
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    assert all(word.surface in record["sentence"] for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(record):
    tokens = get_tagger("es")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_pos2_is_dead_for_spanish():
    """es_core_news_sm has no tagger component: tag_ == pos_, so no fine tag ever reaches pos2 (plan D5)."""
    seen = {token.feature.pos2 for record in RECORDS for token in get_tagger("es")(record["sentence"])}
    assert seen == {""}
    assert ES_EXCLUDED_SUBTYPES == () and ES_ALLOWED_POS == ("ADJ", "ADV", "NOUN", "VERB")


def test_curly_and_straight_apostrophe_twins_mine_alike(parser):
    curly, straight = (next(r for r in RECORDS if r["id"] == rid)["sentence"] for rid in ("es20", "es21"))
    assert {w.mined_form for w in _words(parser, curly)} == {w.mined_form for w in _words(parser, straight)}


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    (word,) = [w for w in _words(parser, "¡NO LO SÉ!") if w.mined_form == "saber"]
    assert word.surface == "SÉ"


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "JUAN: [portazo] - ¿Adónde fuiste?", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"ir"}

"""Italian mining over self-written sentences through the REAL parser and model (A.5 Stage 3 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.it.morphology import IT_ALLOWED_POS, IT_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "it" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every fine tag it_core_news_sm put under ADJ/ADV/NOUN/VERB over this corpus (D4). A new one fails here.
EXPECTED_FINE_TAGS = {"A", "NO", "B", "BN", "S", "V", "V_PC", "V_PC_PC"}


@pytest.fixture
def parser():
    return get_profile("it").create_parser(switch_language(AnkiMinerConfig(), "it"))


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
    assert not any(" " in front for front in mined), record["id"]
    assert all(word.surface in record["sentence"] for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(record):
    tokens = get_tagger("it")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_the_excluded_subtypes_pin_matches_real_output():
    seen = {
        token.feature.pos2
        for record in RECORDS
        for token in get_tagger("it")(record["sentence"])
        if token.feature.pos1 in IT_ALLOWED_POS and token.feature.pos2
    }
    assert seen <= EXPECTED_FINE_TAGS
    assert "BN" in seen and IT_EXCLUDED_SUBTYPES == ("BN",)


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    (word,) = _words(parser, "LA FINE")
    assert (word.mined_form, word.surface, word.pos) == ("fine", "FINE", "NOUN")
    assert "LA FINE"[word.surface_start : word.surface_end] == "FINE"


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "MARCO: [porta che sbatte] - Dove sei andato? ♪", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"dove", "andare"}


def test_the_known_word_front_with_an_elided_article_meets_the_mined_noun(parser):
    fold = get_profile("it").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "Ho bevuto dell'acqua.") if w.mined_form == "acqua"]
    assert fold("l'acqua") == fold(word.mined_form)

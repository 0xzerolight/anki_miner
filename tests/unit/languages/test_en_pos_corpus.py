"""English mining over self-written sentences through the REAL parser and model (A.5 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.en.morphology import EN_ALLOWED_POS, EN_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "en" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every fine tag en_core_web_sm put under ADJ/ADV/NOUN/VERB over this corpus (D14). A new one fails here.
EXPECTED_FINE_TAGS = {
    "NN",
    "NNS",
    "VB",
    "VBD",
    "VBG",
    "VBN",
    "VBP",
    "VBZ",
    "JJ",
    "JJR",
    "JJS",
    "RB",
    "RBR",
    "RBS",
    "WRB",
}


@pytest.fixture
def parser():
    return get_profile("en").create_parser(switch_language(AnkiMinerConfig(), "en"))


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
    tokens = get_tagger("en")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_the_excluded_subtypes_pin_matches_real_output():
    seen = {
        token.feature.pos2
        for record in RECORDS
        for token in get_tagger("en")(record["sentence"])
        if token.feature.pos1 in EN_ALLOWED_POS and token.feature.pos2
    }
    assert seen <= EXPECTED_FINE_TAGS
    assert EN_EXCLUDED_SUBTYPES == ()


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    (word,) = _words(parser, "THE END")
    assert (word.mined_form, word.surface, word.pos) == ("end", "END", "NOUN")
    assert "THE END"[word.surface_start : word.surface_end] == "END"


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "JOHN: [door slams] - Where did you go?", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"go"}


def test_the_known_word_front_to_go_meets_the_mined_go(parser):
    fold = get_profile("en").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "To go or not to go.") if w.mined_form == "go"][:1]
    assert fold("to go") == fold(word.mined_form)

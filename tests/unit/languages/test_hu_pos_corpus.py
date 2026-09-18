"""Hungarian mining over the fixture corpus through the REAL model and parser (E.9, Stage D fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.hu.morphology import HU_ALLOWED_POS, HU_EXCLUDED_SUBTYPES
from anki_miner.languages.hu.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "hu" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every ``pos2`` hu_core_news_md puts on an ADJ/ADV/NOUN/VERB token over this corpus. ``pos2`` is the *tagger's*
#: UPOS guess recorded only when it disagrees with the morphologizer's (``_spaced/tokens.py:73``), so the labels are
#: UPOS names, not a fine tagset — hu's one case is ``Házakban``, a NOUN the tagger calls ADJ. Nothing gates on it
#: (``HU_EXCLUDED_SUBTYPES`` is empty); a new value here is a model change worth seeing.
EXPECTED_POS2 = {"ADJ"}


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


@pytest.fixture(scope="module")
def parser():
    return get_profile("hu").create_parser(switch_language(AnkiMinerConfig(), "hu"))


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


def test_the_excluded_subtypes_pin_matches_real_output(tagger):
    seen = {
        token.feature.pos2
        for record in RECORDS
        for token in tagger(record["sentence"])
        if token.feature.pos1 in HU_ALLOWED_POS and token.feature.pos2
    }
    assert seen == EXPECTED_POS2
    assert HU_EXCLUDED_SUBTYPES == ()  # nothing to exclude: the labels are UPOS names, not a fine tagset
    assert switch_language(AnkiMinerConfig(), "hu").excluded_subtypes == ()


def test_a_separated_preverb_fronts_the_joined_verb(parser):
    assert "elolvas" in {word.mined_form for word in _words(parser, "Nem olvasta el a könyvet.")}
    assert "hazamegy" in {word.mined_form for word in _words(parser, "Haza akarok menni.")}


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    sentence = "ŐSZINTÉN SZÓLVA NEM ÉRTEM AZ ŰRHAJÓT."
    fronts = {word.mined_form: word for word in _words(parser, sentence)}
    assert {"őszinte", "űrhajó"} <= set(fronts)
    word = fronts["űrhajó"]
    assert word.surface == "ŰRHAJÓT"
    assert sentence[word.surface_start : word.surface_end] == "ŰRHAJÓT"


def test_the_question_clitic_and_the_abbreviations_never_mine(parser):
    assert not {"-e", "-E"} & {word.mined_form for word in _words(parser, "TUDOD-E, HOL VAN A HÁZ?")}
    fronts = {word.mined_form for word in _words(parser, "Pl. az alma és a körte stb. gyümölcs.")}
    assert {"alma", "körte", "gyümölcs"} <= fronts
    assert not {"pl.", "stb.", "pl", "stb"} & fronts


def test_the_hungarian_sdh_default_strips_a_double_acute_label(parser):
    words = _words(parser, "GYŐZŐ: [ajtó csapódik] - Hol van a kulcs?", subtitle_cleanup=True)
    fronts = {word.mined_form for word in words}
    assert "kulcs" in fronts
    assert not {"győző", "GYŐZŐ", "ajtó", "csapódik"} & fronts


def test_the_known_word_front_a_haz_meets_the_mined_haz(parser):
    fold = get_profile("hu").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "A diák elolvasott egy könyvet a házban.") if w.mined_form == "ház"]
    assert fold("a ház") == fold(word.mined_form)

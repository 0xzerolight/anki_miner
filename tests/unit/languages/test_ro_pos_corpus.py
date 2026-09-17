"""Romanian mining over self-written dialogue lines through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.ro.morphology import RO_ALLOWED_POS
from anki_miner.languages.ro.tokenizer import build_tagger
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "ro" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
CEDILLA_SCIENCE = "\u015etiin\u0163a e grea."  # \u015etiin\u0163a e grea.


# Module scope: the parser and the tagger keep their own model reference, so conftest's per-test
# tagger-cache reset does not reload ro_core_news_sm for every record.
@pytest.fixture(scope="module")
def parser():
    return get_profile("ro").create_parser(switch_language(AnkiMinerConfig(), "ro"))


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


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
    stored = get_profile("ro").normalize(record["sentence"])
    assert all(word.surface in stored for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    tokens = tagger(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_pos2_is_live_over_the_corpus(tagger):
    """E.1: ro has a trained tagger, so excluded_subtypes is real config (unlike ca)."""
    tokens = [token for record in RECORDS for token in tagger(record["sentence"])]
    content = [token for token in tokens if token.feature.pos1 in RO_ALLOWED_POS]
    assert content and all(token.feature.pos2 and token.feature.pos2 != token.feature.pos1 for token in content)


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    words = [w for w in _words(parser, "SFÂRȘITUL FILMULUI") if w.mined_form == "sfârșit"]
    assert [(w.surface, w.pos) for w in words] == [("SFÂRȘITUL", "NOUN")]


def test_a_cedilla_line_is_stored_and_bolded_in_comma_below(parser):
    words = _words(parser, CEDILLA_SCIENCE)
    (word,) = [w for w in words if w.mined_form == "știință"]
    assert word.surface == "Știința"
    assert "\u015e" not in word.sentence and "\u0163" not in word.sentence


def test_the_sdh_default_strips_a_romanian_speaker_label_before_tagging(parser):
    words = _words(parser, "POLIȚISTUL: [sirene] Stai pe loc!", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"sta", "loc"}


def test_a_deck_front_with_an_article_meets_the_mined_noun(parser):
    fold = get_profile("ro").dedup_fold
    assert fold is not None
    (carte,) = [w for w in _words(parser, "Am citit o carte.") if w.mined_form == "carte"]
    assert fold("o carte") == fold(carte.mined_form)

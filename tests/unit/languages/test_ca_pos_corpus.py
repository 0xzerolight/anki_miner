"""Catalan mining over self-written sentences through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ca.morphology import CA_ALLOWED_POS
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "ca" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


# Module scope: the parser and the tagger keep their own model reference, so conftest's per-test
# tagger-cache reset does not reload ca_core_news_sm for every record (~1.5 s each).
@pytest.fixture(scope="module")
def parser():
    return get_profile("ca").create_parser(switch_language(AnkiMinerConfig(), "ca"))


@pytest.fixture(scope="module")
def tagger():
    return get_tagger("ca")


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


def test_pos2_is_dead_over_the_whole_corpus(tagger):
    """E.2.1 / D11: no trained tagger, so excluded_subtypes can never match anything."""
    tokens = [token for record in RECORDS for token in tagger(record["sentence"])]
    assert tokens and all(token.feature.pos2 == "" for token in tokens)
    assert any(token.feature.pos1 in CA_ALLOWED_POS for token in tokens)


def test_an_all_caps_cue_bolds_the_original_surface(parser):
    words = [w for w in _words(parser, "EL FINAL") if w.mined_form == "final"]
    assert [(w.surface, w.pos) for w in words] == [("FINAL", "NOUN")]


def test_the_legacy_l_dot_letter_is_normalised_before_tagging(parser):
    words = _words(parser, "El coŀlegi és a prop.")
    assert "col·legi" in {word.mined_form for word in words}
    assert all("ŀ" not in word.sentence for word in words)


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "PERE: [soroll] Tinc gana.", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"tenir", "gana"}


def test_a_deck_front_with_an_elided_article_meets_the_mined_noun(parser):
    fold = get_profile("ca").dedup_fold
    assert fold is not None
    (home,) = [w for w in _words(parser, "L'home va comprar un llibre.") if w.mined_form == "home"]
    assert fold("l'home") == fold(home.mined_form)

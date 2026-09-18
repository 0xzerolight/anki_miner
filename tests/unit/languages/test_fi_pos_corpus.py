"""Finnish mining over self-written sentences through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import tagger_provider
from anki_miner.languages.fi.morphology import FI_ALLOWED_POS, FI_EXCLUDED_SUBTYPES
from anki_miner.languages.fi.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "fi" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every TDT fine tag fi_core_news_sm put under ADJ/ADV/NOUN/VERB over this corpus. A new one fails here and is
#: judged against FI_EXCLUDED_SUBTYPES (F3).
FI_OBSERVED_FINE_TAGS = {"A", "Adv", "Adv_V", "N", "Num", "Pron", "V"}


@pytest.fixture(scope="module")
def finnish_tagger():
    """One model load for the module: conftest clears ``tagger_provider._TAGGERS`` around every test."""
    return build_tagger()


@pytest.fixture(autouse=True)
def _reuse_the_finnish_tagger(finnish_tagger, monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, "fi", finnish_tagger)


@pytest.fixture
def parser():
    return get_profile("fi").create_parser(switch_language(AnkiMinerConfig(), "fi"))


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
    tokens = get_tagger("fi")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_pos2_is_live_and_the_fine_tags_under_allowed_classes_are_pinned():
    tokens = [token for record in RECORDS for token in get_tagger("fi")(record["sentence"])]
    seen = {token.feature.pos2 for token in tokens if token.feature.pos1 in FI_ALLOWED_POS and token.feature.pos2}
    assert seen <= FI_OBSERVED_FINE_TAGS
    assert all(token.feature.pos2 for token in tokens)  # TDT tags never equal a UPOS name
    # The exclusion is exercised, not merely declared: fi26's miksei halves are ADV/Adv_V, and the gate is what
    # keeps them off the card (the record's must_not_mine). Both excluded tags are in the model's own tagset.
    assert FI_EXCLUDED_SUBTYPES == ("Adv_V", "C_V")
    assert "Adv_V" in seen
    excluded_surfaces = {
        token.surface
        for token in tokens
        if token.feature.pos1 in FI_ALLOWED_POS and token.feature.pos2 in FI_EXCLUDED_SUBTYPES
    }
    assert excluded_surfaces == {"miks", "ei"}


def test_a_shouted_cue_bolds_the_original_surface(parser):
    line = "TÄMÄ ON LOPPU."
    (loppu,) = [word for word in _words(parser, line) if word.mined_form == "loppu"]
    assert loppu.surface == "LOPPU" and line[loppu.surface_start : loppu.surface_end] == "LOPPU"


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "MATTI: [ovi paukahtaa] - Missä kirjat ovat?", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"kirja"}


def test_the_part_of_speech_field_names_the_class(parser):
    (kirja,) = [word for word in _words(parser, "Kirjassa oli kuvia.") if word.mined_form == "kirja"]
    assert get_profile("fi").render_hooks[0].render(kirja, config=AnkiMinerConfig()) == {"pos": "noun"}

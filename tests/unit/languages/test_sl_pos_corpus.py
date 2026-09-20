"""Slovenian mining over real sentences through the REAL parser and model (E.9 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sl"
RECORDS = [
    json.loads(line)
    for line in (FIXTURES / "pos_corpus.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]


# Module scope: conftest's per-test tagger-cache reset would otherwise reload sl_core_news_sm per record.
@pytest.fixture(scope="module")
def parser():
    return get_profile("sl").create_parser(switch_language(AnkiMinerConfig(), "sl"))


@pytest.fixture(scope="module")
def tagger():
    return get_tagger("sl")


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
    line = get_profile("sl").normalize(record["sentence"])
    assert "".join(token.surface for token in tagger(line)) == line.replace(" ", "")


def test_the_dual_mines_its_lemma_like_any_other_number(parser):
    """Decision 2 on real output: the dual row mines its lemma, and no card carries a Plural field."""
    mined = {word.mined_form for word in _words(parser, "Dve knjigi sta na mizi.")}
    assert {"knjiga", "miza"} <= mined
    assert "noun_plural" not in get_profile("sl").render_hooks[1].field_names()

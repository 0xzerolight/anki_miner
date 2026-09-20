"""th POS defaults, and Thai mining over real sentences (C.3 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.th.pos import TH_ALLOWED_POS, TH_EXCLUDED_SUBTYPES, TH_POS_LABELS
from anki_miner.languages.th.tokenizer import build_tagger

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "th" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def test_allowed_pos_is_the_four_content_classes():
    assert TH_ALLOWED_POS == ("NOUN", "VERB", "ADJ", "ADV")


def test_excluded_subtypes_are_the_two_tiers():
    assert TH_EXCLUDED_SUBTYPES == ("stopword", "mark")


def test_every_allowed_tag_and_every_tier_has_a_label():
    for tag in TH_ALLOWED_POS:
        assert TH_POS_LABELS[tag]
    for tier in TH_EXCLUDED_SUBTYPES:
        assert TH_POS_LABELS[tier]
    assert "PROPN" in TH_POS_LABELS and "PROPN" not in TH_ALLOWED_POS


@pytest.mark.parametrize("record", RECORDS, ids=[r["id"] for r in RECORDS])
def test_corpus_lines_tier_as_expected(tagger, record):
    mineable = {
        token.surface
        for token in tagger.parse(record["sentence"])
        if token.feature.pos1 in TH_ALLOWED_POS and token.feature.pos2 not in TH_EXCLUDED_SUBTYPES
    }
    assert set(record["must_mine"]) <= mineable, record["id"]
    assert not set(record["must_not_mine"]) & mineable, record["id"]

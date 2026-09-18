"""Particle verbs on REAL sv_core_news_sm arcs: the stash, the spaced join and the attested front (N3).

``particle_verbs.jsonl`` records, per self-written sentence, the model's arc and lemma, the one candidate with its
existence in wty-sv-en 2026.08.29 (a fact about headwords, not dictionary content), and the fronts the pass must
produce with that dictionary and without one. ``gold`` is the linguistically right front; a row whose expectation
differs from it is a recorded miss (see its ``note``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import SeparableVerbPass
from anki_miner.languages.registry import get_profile
from anki_miner.languages.sv.morphology import swedish_particle_candidates
from anki_miner.languages.sv.tokenizer import build_tagger
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sv" / "particle_verbs.jsonl"
ROWS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
ARCS = [row for row in ROWS if row["head"]]
MISSES = [row for row in ROWS if not row["head"]]


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _head(tagger, row):
    tokens = tagger(row["sentence"])
    heads = [t for t in tokens if t.surface == row["head"] and getattr(t.feature, "particle", "") == row["particle"]]
    assert heads, (row["id"], [(t.surface, getattr(t.feature, "particle", "")) for t in tokens])
    return tokens, heads[0]


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_arc_lemma_and_candidate_are_the_recorded_ones(tagger, row):
    _tokens, head = _head(tagger, row)
    assert head.feature.lemma == row["model_lemma"]
    assert swedish_particle_candidates(head) == list(row["candidates"])


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_front_is_the_attested_join_with_a_dictionary_and_the_join_without_one(tagger, row):
    for attest, expected in (
        (lambda words: {w for w in words if row["candidates"].get(w)}, "expect_attested"),
        (None, "expect_unattested"),
    ):
        tokens, head = _head(tagger, row)
        SeparableVerbPass(candidates=swedish_particle_candidates)(tokens, attest, None)
        assert head.feature.lemma == row[expected], (row["id"], expected)
        assert head.feature.particle == ""


@pytest.mark.parametrize("row", MISSES, ids=[row["id"] for row in MISSES])
def test_a_recorded_parse_miss_mines_the_bare_verb(tagger, row):
    tokens = tagger(row["sentence"])
    assert all(not getattr(t.feature, "particle", "") for t in tokens), row["id"]
    parser = get_profile("sv").create_parser(switch_language(AnkiMinerConfig(), "sv"))
    units = [ReadingUnit(text=row["sentence"], index=0, location_label="t")]
    fronts = {word.mined_form for word in parser.parse_text_units(units, False)[0]}
    assert row["gold"] not in fronts, row["id"]

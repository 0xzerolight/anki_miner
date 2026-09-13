"""Separable verbs on REAL nl_core_news_sm arcs: the stash, the Dutch candidate order and the attested join (N3).

``separable_verbs.jsonl`` records, per self-written sentence, the model's arc and lemma, the ordered candidates
with their existence in wty-nl-en 2026.08.29 (facts about headwords, not dictionary content), and the fronts the
pass must produce with that dictionary and without one. ``gold`` is the linguistically right front; rows whose
expectation differs from it are real, pinned misses (see each ``note``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import SeparableVerbPass, particle_plus_lemma
from anki_miner.languages.nl.morphology import dutch_particle_candidates
from anki_miner.languages.nl.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "nl" / "separable_verbs.jsonl"
ROWS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
ARCS = [row for row in ROWS if row["head"]]
MISSES = [row for row in ROWS if not row["head"]]


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


def _head(tagger, row):
    tokens = tagger(row["sentence"])
    heads = [t for t in tokens if t.surface == row["head"] and getattr(t.feature, "particle", "") == row["particle"]]
    assert heads, (row["id"], [(t.surface, getattr(t.feature, "particle", "")) for t in tokens])
    return tokens, heads[0]


def _attest(row):
    return lambda words: {word for word in words if row["candidates"].get(word)}


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_arc_lemma_and_candidate_order_are_the_recorded_ones(tagger, row):
    _tokens, head = _head(tagger, row)
    assert head.feature.lemma == row["model_lemma"]
    assert dutch_particle_candidates(head) == list(row["candidates"])


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_join_with_the_dictionary(tagger, row):
    tokens, head = _head(tagger, row)
    SeparableVerbPass(candidates=dutch_particle_candidates)(tokens, _attest(row), None)
    assert head.feature.lemma == row["expect_attested"]


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_join_without_a_dictionary(tagger, row):
    tokens, head = _head(tagger, row)
    SeparableVerbPass(candidates=dutch_particle_candidates)(tokens, None, None)
    assert head.feature.lemma == row["expect_unattested"]


@pytest.mark.parametrize("row", MISSES, ids=[row["id"] for row in MISSES])
def test_a_parse_miss_stashes_nothing(tagger, row):
    assert not [t for t in tagger(row["sentence"]) if getattr(t.feature, "particle", "")], row["note"]


def test_the_dutch_order_beats_particle_plus_lemma_on_this_corpus(tagger):
    def score(candidates, with_dictionary: bool) -> int:
        right = 0
        for row in ARCS:
            tokens, head = _head(tagger, row)
            SeparableVerbPass(candidates=candidates)(tokens, _attest(row) if with_dictionary else None, None)
            right += head.feature.lemma == row["gold"]
        return right

    assert (score(particle_plus_lemma, True), score(particle_plus_lemma, False)) == (38, 36)
    assert (score(dutch_particle_candidates, True), score(dutch_particle_candidates, False)) == (45, 46)


@pytest.mark.parametrize(
    ("term_lookup", "front"), [(None, "meegaan"), (lambda words: {"meegaan", "overgaan"}, "meegaan")]
)
def test_the_parser_mines_the_joined_infinitive(term_lookup, front):
    config = switch_language(AnkiMinerConfig(), "nl")
    parser = get_profile("nl").create_parser(config, term_lookup=term_lookup)
    units = [ReadingUnit(text="Hij hoopt mee te gaan.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    fronts = {word.mined_form for word in words}
    assert front in fronts and "overgaan" not in fronts

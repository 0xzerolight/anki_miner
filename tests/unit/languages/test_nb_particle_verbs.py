"""Particle verbs on REAL nb_core_news_sm arcs: the stash, the written-apart candidate and the attested join (B4).

``particle_verbs.jsonl`` records, per self-written sentence, the model's arc and lemma, the nb candidates, the
existence in wty-nb-en 2026.08.29 of both the nb candidate and the shared default's particle + lemma join (facts
about headwords, not dictionary content), and the fronts the pass must produce with that dictionary and without
one. ``gold`` is the linguistically right front; rows whose expectation differs from it are real, pinned misses
(see each ``note``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import SeparableVerbPass, particle_plus_lemma
from anki_miner.languages.nb.morphology import norwegian_particle_candidates
from anki_miner.languages.nb.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "nb" / "particle_verbs.jsonl"
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
    return lambda words: {word for word in words if row["attested"].get(word)}


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_arc_lemma_and_candidates_are_the_recorded_ones(tagger, row):
    _tokens, head = _head(tagger, row)
    assert head.feature.lemma == row["model_lemma"]
    assert norwegian_particle_candidates(head) == row["candidates"]


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_join_with_the_dictionary(tagger, row):
    tokens, head = _head(tagger, row)
    SeparableVerbPass(candidates=norwegian_particle_candidates)(tokens, _attest(row), None)
    assert head.feature.lemma == row["expect_attested"]


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_join_without_a_dictionary(tagger, row):
    tokens, head = _head(tagger, row)
    SeparableVerbPass(candidates=norwegian_particle_candidates)(tokens, None, None)
    assert head.feature.lemma == row["expect_unattested"]


@pytest.mark.parametrize("row", MISSES, ids=[row["id"] for row in MISSES])
def test_a_parse_without_the_arc_stashes_nothing(tagger, row):
    assert not [t for t in tagger(row["sentence"]) if getattr(t.feature, "particle", "")], row["note"]


def test_the_written_apart_order_beats_the_shared_default_on_this_corpus(tagger):
    def score(candidates, with_dictionary: bool) -> int:
        right = 0
        for row in ARCS:
            tokens, head = _head(tagger, row)
            SeparableVerbPass(candidates=candidates)(tokens, _attest(row) if with_dictionary else None, None)
            right += head.feature.lemma == row["gold"]
        return right

    assert (score(norwegian_particle_candidates, True), score(norwegian_particle_candidates, False)) == (17, 25)
    assert (score(particle_plus_lemma, True), score(particle_plus_lemma, False)) == (3, 0)


def test_the_shared_default_would_attest_other_verbs(tagger):
    """particle + lemma is a real headword on 8 arcs, each a different verb (oppstå "arise" for står opp)."""
    wrong = sorted(
        {row["particle"] + row["model_lemma"] for row in ARCS if row["attested"][row["particle"] + row["model_lemma"]]}
    )
    assert wrong == ["oppfinne", "oppgi", "oppstå", "oppta", "pålegge"]
    assert sum(row["attested"][row["particle"] + row["model_lemma"]] for row in ARCS) == 8


@pytest.mark.parametrize(
    ("term_lookup", "front"), [(None, "stå opp"), (lambda words: {w for w in words if w == "stå opp"}, "stå opp")]
)
def test_the_parser_mines_the_particle_verb(term_lookup, front):
    config = switch_language(AnkiMinerConfig(), "nb")
    parser = get_profile("nb").create_parser(config, term_lookup=term_lookup)
    units = [ReadingUnit(text="Jeg står opp klokka sju.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    fronts = {word.mined_form for word in words}
    assert front in fronts and "oppstå" not in fronts and "opp" not in fronts


def test_an_unattested_join_keeps_the_verb_when_a_dictionary_is_wired():
    config = switch_language(AnkiMinerConfig(), "nb")
    parser = get_profile("nb").create_parser(config, term_lookup=lambda words: set())
    units = [ReadingUnit(text="Hun gikk hjem.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    assert {word.mined_form for word in words} == {"gå"}

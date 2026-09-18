"""Particle verbs on REAL da_core_news_sm arcs: the stash, the two-word candidate, and what is NOT taken (DA5).

``particle_verbs.jsonl`` records, per self-written sentence, the model's arc and lemma, the Danish candidate, the
existence in wty-da-en 2026.08.29 of both the Danish candidate and the shared default's ``particle + lemma`` join
(facts about headwords, not dictionary content), and the fronts the pass must produce with a dictionary and without
one. ``gold`` is the linguistically right front; rows whose expectation differs from it are real, pinned misses
(see each ``note``). Rows with an empty ``head`` are the recorded known limit: the model puts most Danish particles
on ``advmod``/``advmod:lmod``, which ``DA_SEPARABLE_VERB_DEPS`` deliberately does not take, because taking them
would join 50 more verbs and silently demote ~170 more ordinary adverbs per 3,000 sentences (plan DA5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import SeparableVerbPass, particle_plus_lemma
from anki_miner.languages.da.morphology import danish_particle_candidates
from anki_miner.languages.da.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "da" / "particle_verbs.jsonl"
ROWS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
ARCS = [row for row in ROWS if row["head"]]
LIMITS = [row for row in ROWS if not row["head"]]


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
    assert danish_particle_candidates(head) == row["candidates"]


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_join_with_the_dictionary(tagger, row):
    tokens, head = _head(tagger, row)
    SeparableVerbPass(candidates=danish_particle_candidates)(tokens, _attest(row), None)
    assert head.feature.lemma == row["expect_attested"]


@pytest.mark.parametrize("row", ARCS, ids=[row["id"] for row in ARCS])
def test_the_join_without_a_dictionary(tagger, row):
    tokens, head = _head(tagger, row)
    SeparableVerbPass(candidates=danish_particle_candidates)(tokens, None, None)
    assert head.feature.lemma == row["expect_unattested"]


@pytest.mark.parametrize("row", LIMITS, ids=[row["id"] for row in LIMITS])
def test_an_adverbial_arc_stashes_nothing_and_leaves_the_adverb_mineable(tagger, row):
    """DA5's known limit, pinned from both sides: no join, but the adverb survives as a word."""
    tokens = tagger(row["sentence"])
    assert not [t for t in tokens if getattr(t.feature, "particle", "")], row["note"]
    assert row["adverb"] in {t.surface.casefold() for t in tokens}
    assert "PART" not in {t.feature.pos1 for t in tokens}


def test_an_unattested_compound_prt_join_keeps_the_verb_and_loses_the_particle(tagger):
    """The other half of the tradeoff: a stashed particle the dictionary cannot attest is gone from mining."""
    tokens = tagger("Hun vendte sig om og så på ham.")
    (particle,) = [t for t in tokens if t.surface == "om"]
    assert particle.feature.pos1 == "PART"
    SeparableVerbPass(candidates=danish_particle_candidates)(tokens, lambda words: set(), None)
    (head,) = [t for t in tokens if t.surface == "vendte"]
    assert head.feature.lemma == "vende" and particle.feature.pos1 == "PART"


def test_the_two_word_order_beats_the_shared_default(tagger):
    def score(candidates, with_dictionary: bool) -> int:
        right = 0
        for row in ARCS:
            tokens, head = _head(tagger, row)
            SeparableVerbPass(candidates=candidates)(tokens, _attest(row) if with_dictionary else None, None)
            right += head.feature.lemma == row["gold"]
        return right

    assert score(danish_particle_candidates, True) > score(particle_plus_lemma, True)
    # opgive, opstaa, paatag, omvende: every fused form wty knows is a DIFFERENT verb.
    assert score(particle_plus_lemma, False) == 0


@pytest.mark.parametrize(
    ("term_lookup", "front"), [(None, "give op"), (lambda words: {w for w in words if w == "give op"}, "give op")]
)
def test_the_parser_mines_the_particle_verb(term_lookup, front):
    config = switch_language(AnkiMinerConfig(), "da")
    parser = get_profile("da").create_parser(config, term_lookup=term_lookup)
    units = [ReadingUnit(text="Hun gav op efter en time.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    fronts = {word.mined_form for word in words}
    assert front in fronts and "opgive" not in fronts and "op" not in fronts


def test_an_unattested_join_keeps_the_verb_when_a_dictionary_is_wired():
    config = switch_language(AnkiMinerConfig(), "da")
    parser = get_profile("da").create_parser(config, term_lookup=lambda words: set())
    units = [ReadingUnit(text="Hun vendte sig om.", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    assert "vende" in {word.mined_form for word in words}

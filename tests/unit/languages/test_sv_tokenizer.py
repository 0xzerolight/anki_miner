"""The Swedish tagger's configuration and the parser's particle join (real sv_core_news_sm)."""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import SeparableVerbPass
from anki_miner.languages.sv.morphology import swedish_particle_candidates
from anki_miner.languages.sv.parser import create_parser
from anki_miner.languages.sv.tokenizer import build_tagger
from anki_miner.services.tagger import LockedTagger


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


def test_the_tagger_keeps_the_parser_and_repairs_a_capitalised_lemma(tagger):
    assert isinstance(tagger, LockedTagger)
    assert "parser" in tagger.nlp.pipe_names and "tagger" in tagger.nlp.pipe_names
    fronts = {token.surface: token.feature.lemma for token in tagger("Huset var stort.")}
    assert fronts["Huset"] == "hus"  # without the opt-in the lemma is Huset, lowered to huset
    assert fronts["stort"] == "stor"


def test_a_hyphen_compound_and_an_abbreviation_stay_one_token_each(tagger):
    surfaces = [token.surface for token in tagger("Hon skickade ett e-postmeddelande, t.ex. i dag.")]
    assert "e-postmeddelande" in surfaces and "t.ex." in surfaces


def test_a_pruned_exception_no_longer_eats_the_sentence_dot(tagger):
    tokens = tagger("Hon är ung.")
    assert [token.surface for token in tokens][-2:] == ["ung", "."]


def test_the_parser_carries_the_swedish_particle_order(monkeypatch):
    seen = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert create_parser(AnkiMinerConfig()) == "parser"
    pass_ = seen["token_post_pass"]
    assert isinstance(pass_, SeparableVerbPass)
    assert pass_._candidates is swedish_particle_candidates  # noqa: SLF001 - the injected order is the contract

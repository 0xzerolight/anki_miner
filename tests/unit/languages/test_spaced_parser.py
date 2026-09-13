"""create_spaced_parser: the setdefault seams, through a registered stub profile and a stub tagger."""

from __future__ import annotations

import dataclasses
import unicodedata

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import registry, tagger_provider
from anki_miner.languages._spaced import create_spaced_parser
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.tokens import to_duck_tokens
from anki_miner.models.reading import ReadingUnit
from tests.unit.languages.test_spaced_tokens import fake_doc

CODE = "xs"
LINE = "Er sieht den Film an"
ROWS = [
    ("Er", "PRON", "PPER", "er", "sb", 1),
    ("sieht", "VERB", "VVFIN", "sehen", "ROOT", 1),
    ("den", "DET", "ART", "der", "nk", 3),
    ("Film", "NOUN", "NN", "Film", "oa", 1),
    ("an", "ADP", "PTKVZ", "an", "svp", 1),
]


class StubTagger:
    """Tags LINE from ROWS; any other text (the parser re-tags a lemma for its reading) one X per word."""

    def __call__(self, text: str):
        rows = ROWS if text == LINE else [(word, "X", "X", word, "dep", i) for i, word in enumerate(text.split())]
        return to_duck_tokens(fake_doc(text, rows), text, particle_deps=frozenset({"svp"}))


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@pytest.fixture
def config(monkeypatch):
    profile = dataclasses.replace(
        registry.get_profile("ja"),
        code=CODE,
        create_parser=create_spaced_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        normalize=nfc,
        dict_keys=CasefoldDictKeys(),
    )
    monkeypatch.setitem(registry._BUILDERS, CODE, lambda: profile)
    monkeypatch.setitem(registry._CACHE, CODE, profile)
    monkeypatch.setattr("anki_miner.config.config._LANGUAGE_CODES", (*registry.AVAILABLE_LANGUAGES, CODE))
    monkeypatch.setitem(tagger_provider._TAGGERS, CODE, StubTagger())
    cfg = dataclasses.replace(AnkiMinerConfig(), language=CODE, allowed_pos=("NOUN", "VERB"), excluded_subtypes=())
    assert cfg.language == CODE
    return cfg


def _mined(parser) -> set[str]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=LINE, index=0, location_label="t")], False)
    return {word.mined_form for word in words}


def test_the_factory_fills_the_spaced_seams(config):
    parser = create_spaced_parser(config)
    profile = registry.get_profile(CODE)
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None
    assert parser._token_post_pass is None
    assert parser._mined_form_policy is profile.mined_form


def test_without_a_post_pass_the_bare_verb_is_mined(config):
    assert _mined(create_spaced_parser(config)) == {"sehen", "film"}


def test_the_injected_post_pass_reattaches_an_attested_particle(config):
    parser = create_spaced_parser(config, token_post_pass=SeparableVerbPass(), term_lookup=lambda words: {"ansehen"})
    assert _mined(parser) == {"ansehen", "film"}


def test_an_unattested_particle_verb_keeps_the_bare_verb(config):
    parser = create_spaced_parser(config, token_post_pass=SeparableVerbPass(), term_lookup=lambda words: set())
    assert _mined(parser) == {"sehen", "film"}


def test_an_explicit_argument_wins_over_the_seam(config):
    assert create_spaced_parser(config, normalize=str.upper).normalize is str.upper

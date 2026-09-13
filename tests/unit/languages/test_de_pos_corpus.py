"""German mining over self-written sentences through the REAL parser and model (A.5 Stage 2 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import tagger_provider
from anki_miner.languages.de.morphology import DE_ALLOWED_POS
from anki_miner.languages.de.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "de" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
#: Every STTS tag de_core_news_sm put under ADJ/ADV/NOUN/VERB over this corpus once the particle stash and the
#: abbreviation rule ran. A new one fails here and is judged against DE_EXCLUDED_SUBTYPES.
DE_OBSERVED_FINE_TAGS = {"ADJA", "ADJD", "NN", "PWAV", "VAFIN", "VVFIN", "VVINF", "VVIZU", "VVPP"}


@pytest.fixture(scope="module")
def german_tagger():
    """One model load for the module: conftest clears ``tagger_provider._TAGGERS`` around every test."""
    return build_tagger()


@pytest.fixture(autouse=True)
def _reuse_the_german_tagger(german_tagger, monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, "de", german_tagger)


@pytest.fixture
def parser():
    return get_profile("de").create_parser(switch_language(AnkiMinerConfig(), "de"))


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
    tokens = get_tagger("de")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_the_fine_tags_under_allowed_classes_are_pinned():
    seen = {
        token.feature.pos2
        for record in RECORDS
        for token in get_tagger("de")(record["sentence"])
        if token.feature.pos1 in DE_ALLOWED_POS and token.feature.pos2
    }
    assert seen <= DE_OBSERVED_FINE_TAGS


def test_a_shouted_cue_bolds_the_original_surface(parser):
    words = {word.mined_form: word for word in _words(parser, "DER HUND BELLT DIE GANZE NACHT.")}
    hund = words["Hund"]
    assert (hund.surface, hund.pos) == ("HUND", "NOUN")
    assert "DER HUND BELLT DIE GANZE NACHT."[hund.surface_start : hund.surface_end] == "HUND"


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "ANNA: [Tür knallt] - Wo ist mein Schlüssel?", subtitle_cleanup=True)
    assert {word.mined_form for word in words} == {"wo", "Schlüssel"}


def test_a_predicative_adjective_is_an_adjective_on_the_card(parser):
    (leer,) = [w for w in _words(parser, "Der Kühlschrank ist schon wieder leer.") if w.mined_form == "leer"]
    assert leer.pos == "ADJ"
    assert get_profile("de").render_hooks[0].render(leer, config=AnkiMinerConfig()) == {"pos": "adjective"}


def test_the_reflexive_deck_front_meets_the_mined_verb(parser):
    fold = get_profile("de").dedup_fold
    assert fold is not None
    (word,) = [w for w in _words(parser, "Ich freue mich sehr auf die Ferien.") if w.mined_form == "freuen"]
    assert fold("sich freuen") == fold(word.mined_form)

"""Portuguese mining over self-written sentences through the REAL parser and model (B.8 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.pt.morphology import PT_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "pt" / "pos_corpus.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def parser():
    return get_profile("pt").create_parser(switch_language(AnkiMinerConfig(), "pt"))


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
    tokens = get_tagger("pt")(record["sentence"])
    assert "".join(token.surface for token in tokens) == record["sentence"].replace(" ", "")


def test_no_token_carries_a_fine_tag():
    """D2: pt_core_news_sm has no tagger; TAG is a copy of UPOS, so excluded_subtypes is dead config."""
    tags = {token.feature.pos2 for record in RECORDS for token in get_tagger("pt")(record["sentence"])}
    assert tags == {""}
    assert PT_EXCLUDED_SUBTYPES == ()


def test_an_all_caps_cue_keeps_its_surfaces(parser):
    by_front = {word.mined_form: word.surface for word in _words(parser, "ESTOU MUITO CANSADO HOJE.")}
    assert (by_front["cansado"], by_front["hoje"]) == ("CANSADO", "HOJE")


def test_an_enclitic_host_is_located_in_the_stored_line(parser):
    line = "Dá-me o guarda-chuva, por favor."
    (word,) = [w for w in _words(parser, line) if w.mined_form == "dar"]
    assert word.surface == "Dá"
    assert line[word.surface_start : word.surface_end] == "Dá"


def test_the_sdh_default_strips_cues_before_tagging(parser):
    words = _words(parser, "JOÃO: [porta bate] - Onde está a chave?", subtitle_cleanup=True)
    fronts = {word.mined_form for word in words}
    assert "chave" in fronts
    assert not {"joão", "João", "JOÃO", "porta", "bater"} & fronts


def test_known_word_fronts_meet_the_mined_lemmas(parser):
    fold = get_profile("pt").dedup_fold
    assert fold is not None
    mined = {fold(word.mined_form) for word in _words(parser, "Ela levantou-se e pegou o livro.")}
    assert {fold("levantar-se"), fold("o livro"), fold("pegar")} <= mined

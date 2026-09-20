"""Russian mining over self-written dialogue lines through the REAL parser and model (B.8 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.ru.tokenizer import build_tagger
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "ru" / "tokens.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


# Module scope: the parser and the tagger keep their own model reference, so conftest's per-test
# tagger-cache reset does not reload ru_core_news_sm for every record.
@pytest.fixture(scope="module")
def parser():
    return get_profile("ru").create_parser(switch_language(AnkiMinerConfig(), "ru"))


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _words(parser, sentence: str, **kwargs):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False, **kwargs)
    return words


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words = _words(parser, record["sentence"])
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    stored = get_profile("ru").normalize(record["sentence"])
    assert all(word.surface in stored for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    text = get_profile("ru").normalize(record["sentence"])
    tokens = tagger(text)
    assert "".join(token.surface for token in tokens) == text.replace(" ", "")


def test_the_sdh_default_strips_a_russian_speaker_label_before_tagging(parser):
    """R11: the shared Latin capital class cannot match Cyrillic, so ru ships RU_SUBTITLE_REGEX."""
    words = _words(parser, "ИВАН: [стук] Открой дверь!", subtitle_cleanup=True)
    assert "дверь" in {word.mined_form for word in words}
    assert not {"иван", "ИВАН", "стук"} & {word.mined_form for word in words}


def test_a_stress_marked_line_and_its_plain_twin_mine_the_same_fronts(parser):
    """normalize strips U+0300/U+0301 before tagging, so a stressed subtitle mines like a plain one."""
    acute = "\N{COMBINING ACUTE ACCENT}"
    marked = {w.mined_form for w in _words(parser, f"Он чита{acute}л кни{acute}гу у окна{acute}.")}
    plain = {w.mined_form for w in _words(parser, "Он читал книгу у окна.")}
    assert marked == plain == {"читать", "книга", "окно"}

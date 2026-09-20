"""Ukrainian mining over self-written dialogue lines through the REAL parser and model (B.8 fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.uk.tokenizer import build_tagger
from anki_miner.models.reading import ReadingUnit

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "uk" / "tokens.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"


# Module scope: the parser and the tagger keep their own model reference, so conftest's per-test
# tagger-cache reset does not reload uk_core_news_sm for every record.
@pytest.fixture(scope="module")
def parser():
    return get_profile("uk").create_parser(switch_language(AnkiMinerConfig(), "uk"))


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
    stored = get_profile("uk").normalize(record["sentence"])
    assert all(word.surface in stored for word in words), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    text = get_profile("uk").normalize(record["sentence"])
    tokens = tagger(text)
    assert "".join(token.surface for token in tokens) == text.replace(" ", "")


def test_the_sdh_default_strips_a_ukrainian_speaker_label_before_tagging(parser):
    """R11: Є І Ї Ґ are outside the Latin and Russian capital classes, so uk ships UK_SUBTITLE_REGEX."""
    words = _words(parser, "ІВАН: [стук] Відчини двері!", subtitle_cleanup=True)
    mined = {word.mined_form for word in words}
    assert "двері" in mined
    assert not {"іван", "ІВАН", "стук"} & mined


def test_a_stress_marked_line_and_its_plain_twin_mine_the_same_fronts(parser):
    """normalize strips U+0300/U+0301 before tagging, so a stressed subtitle mines like a plain one."""
    acute = "\N{COMBINING ACUTE ACCENT}"
    marked = {w.mined_form for w in _words(parser, f"Він чита{acute}в кни{acute}жку біля вікна{acute}.")}
    plain = {w.mined_form for w in _words(parser, "Він читав книжку біля вікна.")}
    assert marked == plain == {"читати", "книжка", "вікно"}


def test_an_apostrophe_line_and_its_ascii_twin_mine_the_same_fronts(parser):
    """P3: the fronts agree; the SURFACES differ, because the card keeps the author's character."""
    typographic = _words(parser, f"Він кинув м{RSQUO}яча через паркан.")
    ascii_twin = _words(parser, "Він кинув м'яча через паркан.")
    assert {w.mined_form for w in typographic} == {w.mined_form for w in ascii_twin} == {"кинути", "м'яч", "паркан"}
    assert f"м{RSQUO}яча" in {w.surface for w in typographic}
    assert "м'яча" in {w.surface for w in ascii_twin}

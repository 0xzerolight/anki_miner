"""Vietnamese mining over self-written lines through the REAL parser and engine (spec C.4 fixtures).

``must_mine``/``must_not_mine`` pin the plan's behaviour: compounds are one word with a space
(bác sĩ), names are Np, the stopword tier wins over the classifier tag, a shouted or new-style cue
mines the canonical front. ``note`` records what underthesea gets wrong and is never asserted
(vi17: ``ăn xoài`` over-joined into one verb).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.languages.vi.stopwords import VI_STOPWORDS
from anki_miner.models.reading import ReadingUnit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "vi"
RECORDS = [
    json.loads(line) for line in (FIXTURES / "pos_corpus.jsonl").read_text(encoding="utf-8").splitlines() if line
]


@pytest.fixture(scope="module")
def parser():
    return get_profile("vi").create_parser(switch_language(AnkiMinerConfig(), "vi"))


@pytest.fixture(scope="module")
def tagger():
    return get_tagger("vi")


def _words(parser, *sentences: str):
    units = [ReadingUnit(text=sentence, index=i, location_label="t") for i, sentence in enumerate(sentences)]
    words, _index, counts = parser.parse_text_units(units, False)
    return words, counts


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    words, _counts = _words(parser, record["sentence"])
    mined = {word.mined_form for word in words}
    assert set(record["must_mine"]) <= mined, (record["id"], sorted(mined))
    assert not set(record["must_not_mine"]) & mined, (record["id"], sorted(mined))
    assert not mined & VI_STOPWORDS, record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    line = get_profile("vi").normalize(record["sentence"])
    assert "".join(token.surface for token in tagger(line)).replace(" ", "") == line.replace(" ", "")


def test_both_tone_styles_make_one_card_and_one_count(parser):
    """Spec C.4 dedup fixture: hoà bình and hòa bình are one mined word counted twice."""
    words, counts = _words(parser, "Chúng ta cần hoà bình.", "Chúng ta cần hòa bình.")
    assert [word.mined_form for word in words].count("hòa bình") == 1
    assert counts["hòa bình"] == 2


def test_the_stored_sentence_keeps_the_cue_old_style_and_its_capitals(parser):
    words, _counts = _words(parser, "Chúng ta cần hoà bình.")
    assert {word.sentence for word in words} == {"Chúng ta cần hòa bình."}

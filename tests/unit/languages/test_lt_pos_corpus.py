"""Lithuanian mining over self-written sentences through the REAL parser and model (E.9 fixtures).

Every record's ``note`` records what ``lt_core_news_sm`` gets wrong on that line (D-4). Nothing in ``note`` is
asserted: with ``tag_acc`` .8234 and ``pos_acc`` .9058 the misses are the model's, and a better model or patch
must not turn this file red. ``must_mine``/``must_not_mine`` are the behaviour the plan does pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.lt.morphology import LT_ALLOWED_POS
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lt"
RECORDS = [
    json.loads(line)
    for line in (FIXTURES / "pos_corpus.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
#: D-3/MINOR-6: the fine tags that would have to be excluded if any content word carried one.
NON_VOCABULARY_TAGS = ("dkt.tikr.", "sutr.")


@pytest.fixture(scope="module")
def parser():
    return get_profile("lt").create_parser(switch_language(AnkiMinerConfig(), "lt"))


@pytest.fixture(scope="module")
def tagger():
    return get_tagger("lt")


def _words(parser, sentence: str):
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=sentence, index=0, location_label="t")], False)
    return words


def _normalized(sentence: str) -> str:
    return get_profile("lt").normalize(sentence)


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_corpus_sentences_mine_the_expected_fronts(parser, record):
    mined = {word.mined_form for word in _words(parser, record["sentence"])}
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    assert not any(" " in front for front in mined), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_tokenizer_surfaces_cover_the_line(tagger, record):
    line = _normalized(record["sentence"])
    assert "".join(token.surface for token in tagger(line)) == line.replace(" ", "")


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_no_content_word_carries_a_name_or_abbreviation_fine_tag(tagger, record):
    """The corpus-level form of ``LT_EXCLUDED_SUBTYPES = ()`` (D-3): nothing to exclude.

    An exact fine-tag pin is unmanageable here (246-285 distinct ALKSNIS tags over the wty examples). This is
    the stable negative instead: no token the POS gate would mine is filed as a proper noun or an abbreviation.
    """
    for token in tagger(_normalized(record["sentence"])):
        if token.feature.pos1 in LT_ALLOWED_POS:
            assert not token.feature.pos2.startswith(NON_VOCABULARY_TAGS), (record["id"], token.surface)


def test_pos2_is_live_and_alksnis_shaped(tagger):
    """D-3: lt HAS a trained tagger, unlike el; pos2 is a real ALKSNIS tag users can exclude."""
    assert "tagger" in tagger.nlp.pipe_names
    tags = {token.surface: token.feature.pos2 for token in tagger("Studentas vakar perskaitė įdomią knygą.")}
    assert tags["knygą"].startswith("dkt.") and tags["knygą"] != ""

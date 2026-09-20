"""The yue tokenizer against the real pycantonese engine.

Real-engine test: pycantonese is installed for this session and a skip here would
hide a broken tokenizer at the gate. The tagger is built ONCE per module because
tests/conftest.py clears the shared tagger cache per test and the segmenter model
is 34 MB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.yue.tokenizer import build_tagger

FIXTURE = Path(__file__).parents[2] / "fixtures" / "yue" / "tokens.jsonl"


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def rows():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("row", rows(), ids=lambda row: row["line"][:8])
def test_every_fixture_line_tokenizes_exactly_as_recorded(tagger, row):
    tokens = tagger.parse(row["line"])
    assert [
        {"surface": t.surface, "lemma": t.feature.lemma, "pos1": t.feature.pos1, "pos2": t.feature.pos2} for t in tokens
    ] == [{k: t[k] for k in ("surface", "lemma", "pos1", "pos2")} for t in row["tokens"]]


@pytest.mark.parametrize("row", rows(), ids=lambda row: row["line"][:8])
def test_every_surface_is_a_verbatim_slice_of_its_line(tagger, row):
    cursor = 0
    for token in tagger.parse(row["line"]):
        index = row["line"].find(token.surface, cursor)
        assert index != -1, token.surface
        cursor = index + len(token.surface)


def test_a_word_the_segmenter_joined_across_a_space_keeps_the_spaced_surface(tagger):
    # iter_token_spans DROPS a space-free surface it has to stitch across
    # whitespace (morphology.py:487); the spaced slice is what survives, and the
    # space-free lemma is what reaches the card front.
    token = tagger.parse("今 日好開心")[0]
    assert token.surface == "今 日"
    assert token.feature.lemma == "今日"


def test_the_spaced_surface_still_locates_in_the_line(tagger):
    from anki_miner.services.morphology import iter_token_spans

    line = "今 日好開心"
    spans = list(iter_token_spans(line, tagger.parse(line)))
    assert [line[start:end] for _token, start, end in spans] == ["今 日", "好", "開心"]


def test_stop_words_are_tiered_in_pos2(tagger):
    tokens = {t.feature.lemma: t.feature.pos2 for t in tagger.parse("我今日睇咗一套好好睇嘅戲。")}
    assert tokens["我"] == "stopword"
    assert tokens["好睇"] == ""


def test_an_empty_line_produces_no_tokens(tagger):
    assert tagger.parse("") == []

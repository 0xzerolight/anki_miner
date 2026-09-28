"""The yue tokenizer against the real pycantonese engine.

Real-engine test: pycantonese is installed for this session and a skip here would
hide a broken tokenizer at the gate. The tagger is built ONCE per module because
tests/conftest.py clears the shared tagger cache per test and the segmenter model
is 34 MB.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from anki_miner.languages.yue.pos import YUE_ALLOWED_POS
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


@pytest.mark.parametrize(
    ("line", "phrase"),
    [
        ("埋單，唔該。", "唔該"),
        ("對唔住，我瞓過咗龍。", "對唔住"),
        ("唔好意思，我哋淨係收現金。", "唔好意思"),
        ("知道喇，拜拜。", "拜拜"),
        ("多謝讚賞，我都想早啲收工啫。", "多謝"),
    ],
)
def test_a_polite_set_phrase_carries_a_mineable_tag(tagger, line, phrase):
    # The engine tags every one of these X (HKCanCor's fixed expressions),
    # outside YUE_ALLOWED_POS, so the first-week phrases never became cards.
    tags = {t.feature.lemma: t.feature.pos1 for t in tagger.parse(line)}
    assert tags[phrase] in YUE_ALLOWED_POS


@pytest.mark.parametrize(
    ("line", "particle"),
    [("我哋一齊去睇戲啦。", "啦"), ("我們明天去看電影吧。", "吧"), ("我做緊功課。", "緊")],
)
def test_a_particle_is_tagged_part_wherever_the_engine_puts_it(tagger, line, particle):
    # Measured engine tags: 啦 NOUN, 吧 NOUN, standalone aspect 緊 PROPN. Each is
    # a dictionary headword, so any content tag turned it into a card.
    tags = {t.feature.lemma: t.feature.pos1 for t in tagger.parse(line)}
    assert tags[particle] == "PART"


@pytest.mark.parametrize(
    "line", ["前面塞緊車，行告士打道好唔好？", "我唔係唔覆，係真係好忙咋。", "快啲啦，戲就開場喇。"]
)
def test_no_token_spans_a_punctuation_mark(tagger, line):
    # The segmenter only strips punctuation off a word's ends, so a whole-line
    # segment glued 塞緊車，行告士打道 into one dictionary miss.
    surfaces = [t.surface for t in tagger.parse(line)]
    assert "，" in surfaces
    for surface in surfaces:
        assert len(surface) == 1 or not any(unicodedata.category(char).startswith("P") for char in surface), surface

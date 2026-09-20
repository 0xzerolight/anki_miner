"""The committed fa fixtures are complete and machine-readable.

A fixture that silently lost its ZWNJ (the tool trap) or its Arabic-yeh input
row would make every downstream test pass for the wrong reason, so the corpora
are checked for the exact code points they exist to carry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
DAT_FILES = ("words.dat", "verbs.dat", "iverbs.dat", "iwords.dat", "stopwords.dat")
TOKEN_KEYS = {"surface", "lemma", "pos1", "pos2", "mined"}


def _jsonl(name: str) -> list[dict]:
    lines = FIXTURES.joinpath(name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


@pytest.mark.parametrize("name", DAT_FILES)
def test_every_data_file_is_present_and_lf_only(name):
    text = FIXTURES.joinpath(name).read_text(encoding="utf-8")
    assert text.strip()
    assert "\r" not in text


def test_the_words_subset_is_three_hundred_rows():
    rows = FIXTURES.joinpath("words.dat").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 300
    assert all(len(row.split("\t")) == 3 for row in rows)


def test_the_words_subset_keeps_untagged_rows():
    # The ('0',) tier is a real tier (Decision 7): attested, pos1 "unknown".
    rows = FIXTURES.joinpath("words.dat").read_text(encoding="utf-8").splitlines()
    assert sum(1 for row in rows if row.split("\t")[2] == "0") >= 5


def test_the_verb_table_is_the_whole_upstream_file():
    assert len(FIXTURES.joinpath("verbs.dat").read_text(encoding="utf-8").splitlines()) == 693


def test_the_token_corpus_covers_every_spelling_of_the_smoke_verb():
    lines = {row["line"] for row in _jsonl("tokens.jsonl")}
    stem = "\N{ARABIC LETTER MEEM}\N{ARABIC LETTER FARSI YEH}"
    spellings = {line for line in lines if stem in line}
    assert len(spellings) >= 3
    assert any(ZWNJ in line for line in spellings)


def test_every_token_row_names_its_mined_form():
    for row in _jsonl("tokens.jsonl"):
        assert row["tokens"], row
        assert row["normalized"], row
        for token in row["tokens"]:
            assert set(token) >= TOKEN_KEYS, token


def test_every_informal_token_names_its_formal_spelling():
    for row in _jsonl("tokens.jsonl"):
        for token in row["tokens"]:
            if token["pos2"] == "informal":
                assert token["surface_formal"], token


def test_the_normalize_corpus_carries_the_characters_it_exists_for():
    raws = "".join(row["raw"] for row in _jsonl("normalize.jsonl"))
    outs = "".join(row["normalized"] for row in _jsonl("normalize.jsonl"))
    assert "\N{RIGHT-TO-LEFT MARK}" in raws and "\N{RIGHT-TO-LEFT MARK}" not in outs
    assert "\N{ARABIC LETTER YEH ISOLATED FORM}" in raws
    assert "\N{ARABIC LETTER YEH}" not in outs, "Arabic yeh must be unified to Farsi yeh"
    assert "\N{ARABIC LETTER KAF}" not in outs, "Arabic kaf must be unified to Farsi keheh"

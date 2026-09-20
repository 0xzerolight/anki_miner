"""The Stage F gate, re-run on every CI leg: zeyrek lemmas against 518 hand-labelled tokens (tr plan, spike table)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from anki_miner.languages.tr.morphology import tr_casefold
from anki_miner.languages.tr.tokenizer import build_tagger

GOLD_PATH = Path(__file__).parents[2] / "fixtures" / "tr" / "lemma_gold.jsonl"
GOLD = [json.loads(line) for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def picks():
    """``(set, gold, lemma, pos1)`` per gold token: sets A and B tagged as whole sentences, set C word by word."""
    tagger = build_tagger()
    out = []
    for record in GOLD:
        if record["sentence"]:
            tokens = [token for token in tagger(record["sentence"]) if token.surface[0].isalpha()]
        else:
            tokens = [tagger(surface)[0] for surface, _gold in record["tokens"]]
        assert [token.surface for token in tokens] == [surface for surface, _gold in record["tokens"]], record["id"]
        for (_surface, gold), token in zip(record["tokens"], tokens, strict=True):
            out.append((record["set"], gold, token.feature.lemma, token.feature.pos1))
    return out


def _agreement(picks, sets):
    rows = [row for row in picks if row[0] in sets]
    return sum(tr_casefold(lemma) == tr_casefold(gold) for _set, gold, lemma, _pos in rows), len(rows)


def test_the_fixture_is_the_spike_gold():
    assert Counter(record["set"] for record in GOLD) == {"a": 44, "b": 21, "c": 11}
    assert Counter(record["set"] for record in GOLD for _token in record["tokens"]) == {"a": 211, "b": 96, "c": 211}
    assert all(bool(record["sentence"]) == (record["set"] != "c") for record in GOLD)


def test_the_self_written_sentences_clear_the_ninety_percent_gate(picks):
    hits, total = _agreement(picks, {"a", "b"})
    assert total == 307 and hits / total >= 0.90  # 287/307 = 93.5 % when the plan was written


def test_the_frequency_weighted_words_clear_it_too(picks):
    hits, total = _agreement(picks, {"c"})
    assert total == 211 and hits / total >= 0.90  # 199/211 = 94.3 %


def test_every_verb_pick_is_a_dictionary_infinitive(picks):
    verbs = [lemma for set_, _gold, lemma, pos1 in picks if set_ != "c" and pos1 == "VERB"]
    assert len(verbs) >= 80 and all(lemma.endswith(("mek", "mak")) for lemma in verbs)

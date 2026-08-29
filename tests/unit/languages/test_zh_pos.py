"""zh POS defaults, checked through the real inclusion gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.pos import ZH_ALLOWED_POS, ZH_EXCLUDED_SUBTYPES, ZH_POS_LABELS
from anki_miner.languages.zh.tokenizer import build_tagger
from anki_miner.services.morphology import TokenInclusionRule

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "zh" / "pos_corpus.jsonl"
RULE = TokenInclusionRule(allowed_pos=frozenset(ZH_ALLOWED_POS), excluded_subtypes=frozenset(ZH_EXCLUDED_SUBTYPES))

# (surface, jieba flag, mined?) — flags are jieba.posseg's, split into pos1/pos2
# by the tokenizer exactly as LanguageToken stores them.
CASES = [
    ("书", "n", True),
    ("学习", "v", True),
    ("漂亮", "a", True),
    ("非常", "d", True),
    ("一举两得", "i", True),
    ("今天", "t", True),
    ("家里", "s", True),
    ("里面", "f", True),
    ("有意思", "l", True),
    ("我", "r", True),
    ("他", "r", True),
    # jieba's nz is a catch-all that fires on ordinary vocabulary, not just
    # proper nouns — dropping it silently lost core words like this one.
    ("中文", "nz", True),
    ("北京", "ns", False),
    ("小明", "nr", False),
    ("联合国", "nt", False),
    ("子", "ng", False),
    ("的", "uj", False),
    ("三", "m", False),
    ("本", "q", False),
    ("。", "x", False),
    ("ok", "eng", False),
]


def _token(surface: str, flag: str) -> LanguageToken:
    return LanguageToken(surface=surface, pos1=flag[0], pos2="" if len(flag) == 1 else flag, lemma=surface)


@pytest.mark.parametrize(("surface", "flag", "mined"), CASES)
def test_inclusion_gate_matches_the_zh_defaults(surface: str, flag: str, mined: bool) -> None:
    assert RULE.should_include(_token(surface, flag)) is mined


def test_every_allowed_pos_has_a_label() -> None:
    assert set(ZH_POS_LABELS) == set(ZH_ALLOWED_POS)


def test_excluded_subtypes_are_all_reachable_from_an_allowed_class() -> None:
    # A subtype whose first letter is not an allowed pos1 could never fire.
    assert all(subtype[0] in ZH_ALLOWED_POS for subtype in ZH_EXCLUDED_SUBTYPES)


def _mined(sentence: str) -> set[str]:
    return {t.surface for t in build_tagger()(sentence) if RULE.should_include(t)}


@pytest.mark.parametrize("record", [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()])
def test_corpus_sentences_mine_the_expected_words(record: dict) -> None:
    mined = _mined(record["sentence"])
    assert set(record["must_mine"]) <= mined, record["id"]
    assert not set(record["must_not_mine"]) & mined, record["id"]
    assert all(word in record["sentence"] for word in mined), record["id"]

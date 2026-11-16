"""The verb-nominalizer merge (verb stem + 方/手/様) needs a dictionary entry once a dictionary is wired."""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.services.morphology import SyntheticToken, merge_compound_suffixes


def _verb(surface, lemma, kana):
    feature = SimpleNamespace(pos1="動詞", pos2="一般", lemma=lemma, kana=kana, orthBase=lemma, cForm="連用形-一般")
    return SimpleNamespace(surface=surface, feature=feature)


def _kata():
    feature = SimpleNamespace(pos1="接尾辞", pos2="名詞的", lemma="方", kana="カタ", orthBase="方", cForm=None)
    return SimpleNamespace(surface="方", feature=feature)


def _attest(*entries):
    wanted = set(entries)
    return lambda terms: {term for term in terms if term in wanted}


def test_unattested_nominalizer_leaves_the_verb():
    merged = merge_compound_suffixes([_verb("戦い", "戦う", "タタカイ"), _kata()], attest=_attest("言い方"))

    assert [token.surface for token in merged] == ["戦い", "方"]
    assert not any(isinstance(token, SyntheticToken) for token in merged)


def test_attested_nominalizer_stays_one_word():
    merged = merge_compound_suffixes([_verb("言い", "言う", "イイ"), _kata()], attest=_attest("言い方"))

    assert [token.surface for token in merged] == ["言い方"]


def test_no_dictionary_still_merges():
    merged = merge_compound_suffixes([_verb("戦い", "戦う", "タタカイ"), _kata()])

    assert [token.surface for token in merged] == ["戦い方"]


def test_ii_kata_keeps_the_merge():
    # いい方 is mostly 良い方; apart, the tagger's 言う would be mined.
    merged = merge_compound_suffixes([_verb("いい", "言う", "イイ"), _kata()], attest=_attest())

    assert [token.surface for token in merged] == ["いい方"]

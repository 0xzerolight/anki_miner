"""S6: the UniDic disambiguator strip never truncates a duck token's lemma."""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.languages.token import LanguageToken
from anki_miner.services.morphology import SyntheticToken, extract_lemma


def test_duck_token_keeps_a_hyphenated_lemma():
    assert extract_lemma(LanguageToken("well-known", "ADJ", lemma="well-known")) == "well-known"
    assert extract_lemma(LanguageToken("kupu-kupu", "NOUN", lemma="kupu-kupu")) == "kupu-kupu"
    assert extract_lemma(LanguageToken("e-mail", "NOUN", lemma="e-mail")) == "e-mail"


def test_unidic_disambiguator_is_still_stripped():
    node = SimpleNamespace(surface="スクランブル", feature=SimpleNamespace(lemma="スクランブル-scramble", pos1="名詞"))
    assert extract_lemma(node) == "スクランブル"
    synthetic = SyntheticToken("引く", "動詞", "一般", "引く-他動詞", "ヒク")
    assert extract_lemma(synthetic) == "引く"

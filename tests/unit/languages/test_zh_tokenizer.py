"""jieba tokens satisfy the duck-token contract the ja consumers already impose."""

from __future__ import annotations

from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.tokenizer import JiebaTagger, build_tagger
from anki_miner.services.morphology import SyntheticToken, iter_token_spans
from anki_miner.services.tagger import LockedTagger

SENTENCE = "我昨天看了一部非常漂亮的电影。"


class TestJiebaTagger:
    def test_surfaces_reconstruct_the_source_line(self) -> None:
        tokens = build_tagger()(SENTENCE)
        assert "".join(token.surface for token in tokens) == SENTENCE

    def test_every_token_is_locatable_by_iter_token_spans(self) -> None:
        tokens = build_tagger()(SENTENCE)
        located = list(iter_token_spans(SENTENCE, tokens))
        assert len(located) == len(tokens)
        for token, start, end in located:
            assert SENTENCE[start:end] == token.surface

    def test_feature_shape_matches_the_contract(self) -> None:
        for token in build_tagger()(SENTENCE):
            assert isinstance(token, LanguageToken)
            assert len(token.feature.pos1) == 1
            assert token.feature.pos2 == "" or token.feature.pos2.startswith(token.feature.pos1)
            assert token.feature.lemma == token.surface
            assert token.feature.kana == ""

    def test_ja_only_attributes_are_absent(self) -> None:
        token = build_tagger()(SENTENCE)[0]
        for attribute in ("orthBase", "pron", "cType", "cForm", "kanaBase"):
            assert getattr(token.feature, attribute, None) is None

    def test_tokens_are_not_synthetic_tokens(self) -> None:
        # morphology's isinstance gates (:531, :581) are ja-only merge passes.
        assert not any(isinstance(t, SyntheticToken) for t in build_tagger()(SENTENCE))

    def test_multi_letter_flags_split_into_pos1_and_pos2(self) -> None:
        class _Pair:
            def __init__(self, word: str, flag: str) -> None:
                self.word, self.flag = word, flag

        class _Cutter:
            def cut(self, _text: str):
                return [_Pair("北京", "ns"), _Pair("书", "n"), _Pair("", "x")]

        tokens = JiebaTagger(_Cutter())("北京书")
        assert [(t.surface, t.feature.pos1, t.feature.pos2) for t in tokens] == [
            ("北京", "n", "ns"),
            ("书", "n", ""),
        ]


class TestTaggerProvider:
    def test_zh_tagger_is_locked_and_cached(self) -> None:
        first = get_tagger("zh")
        assert isinstance(first, LockedTagger)
        assert get_tagger("zh") is first

    def test_ja_tagger_is_a_different_instance(self) -> None:
        assert get_tagger("zh") is not get_tagger("ja")

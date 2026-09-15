"""jieba tokens satisfy the duck-token contract the ja consumers already impose."""

from __future__ import annotations

import pytest

from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh import tokenizer
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


class _Pair:
    def __init__(self, word: str, flag: str) -> None:
        self.word, self.flag = word, flag


class _RecordingCutter:
    """Cuts one character per token, recording every text it was handed."""

    def __init__(self, drop_last: bool = False) -> None:
        self.seen: list[str] = []
        self._drop_last = drop_last

    def cut(self, text: str):
        self.seen.append(text)
        chars = list(text)
        if self._drop_last and len(self.seen) == 1:
            chars = chars[:-1]
        return [_Pair(char, "n") for char in chars]


class TestSimplifiedCopy:
    """jieba's dictionary is simplified-only, so traditional text is cut on a simplified copy."""

    TRADITIONAL = "我今天去銀行領錢，然後回家看電影。"

    def test_a_traditional_line_splits_like_its_simplified_twin(self) -> None:
        pytest.importorskip("opencc")
        tokens = build_tagger()(self.TRADITIONAL)
        twin = build_tagger()("我今天去银行领钱，然后回家看电影。")
        assert [len(t.surface) for t in tokens] == [len(t.surface) for t in twin]
        assert [t.feature.pos2 or t.feature.pos1 for t in tokens] == [t.feature.pos2 or t.feature.pos1 for t in twin]
        assert "然後" in [t.surface for t in tokens]

    def test_surfaces_are_sliced_from_the_original_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tokenizer, "to_simplified", lambda text: text.replace("後", "后"))
        cutter = _RecordingCutter()
        tokens = JiebaTagger(cutter)("然後")
        assert cutter.seen == ["然后"]
        assert [t.surface for t in tokens] == ["然", "後"]
        assert [t.feature.lemma for t in tokens] == ["然", "後"]

    def test_a_length_changing_conversion_cuts_the_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tokenizer, "to_simplified", lambda text: text + "x")
        cutter = _RecordingCutter()
        JiebaTagger(cutter)("然後")
        assert cutter.seen == ["然後"]

    def test_a_cut_that_does_not_cover_the_text_is_redone_on_the_original(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tokenizer, "to_simplified", lambda text: text.replace("後", "后"))
        cutter = _RecordingCutter(drop_last=True)
        tokens = JiebaTagger(cutter)("然後")
        assert cutter.seen == ["然后", "然後"]
        assert [t.surface for t in tokens] == ["然", "後"]


class TestTaggerProvider:
    def test_zh_tagger_is_locked_and_cached(self) -> None:
        first = get_tagger("zh")
        assert isinstance(first, LockedTagger)
        assert get_tagger("zh") is first

    def test_ja_tagger_is_a_different_instance(self) -> None:
        assert get_tagger("zh") is not get_tagger("ja")

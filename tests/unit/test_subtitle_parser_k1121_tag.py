"""Tagger corrections on the raw token stream (session K1121, lane TAG).

The token tests run the real tagger; the parser tests run the real parser wired
to the benchmark's fixture dictionary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from anki_miner.services import morphology
from anki_miner.services.masu_stem_nominalizer import MasuStemNominalizer

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "parse_corpus" / "k1121_tag.jsonl"


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


needs_tagger = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


@pytest.fixture(scope="module")
def tagger():
    import fugashi

    return fugashi.Tagger()


#: Dictionary tags of the words the tests name (written by hand).
_ENTRY_TAGS: dict[str, str] = {
    "嫌う": "v5u",
    "嫌い": "adj-na n",
    "祭る": "v5r",
    "祭り": "n",
    "頼る": "v5r",
    "頼り": "n",
    "思いきる": "v5r",
    "思いきり": "adv n",
    "はじめる": "v1",
    "はじめ": "n adv",
}


def _lookup(terms: list[str]) -> dict[str, list[tuple[str, str]]]:
    return {term: [("", _ENTRY_TAGS[term])] for term in terms if term in _ENTRY_TAGS}


def _retag(tagger, text: str, lookup=None) -> list:
    return morphology.retag_mistagged_tokens(list(tagger(text)), lookup, tagger)


def _tags(tokens: list) -> list[tuple[str, str]]:
    return [(token.surface, token.feature.pos1) for token in tokens]


def _words(sentence: str) -> set[str]:
    from scripts.parse_benchmark import mine_lite_anchor

    return mine_lite_anchor(sentence)


@needs_tagger
class TestSuffixAfterSpace:
    """A suffix is never spaced from its stem: the clause after the space is tagged alone."""

    @pytest.mark.parametrize(
        ("text", "word", "pos1"),
        [
            ("彼を利用し 周りをだました", "周り", "名詞"),
            ("あの家 金に困っていない", "金", "名詞"),
            ("朝の祭り 人が多い", "人", "名詞"),
            ("今後 君を呼ぶつもりだ", "君", "代名詞"),
            ("明日 ちゃんと話そう", "ちゃんと", "副詞"),
        ],
    )
    def test_the_word_that_opens_the_clause_is_read_alone(self, tagger, text, word, pos1):
        assert (word, "接尾辞") in _tags(list(tagger(text))) or word == "ちゃんと"
        assert (word, pos1) in _tags(_retag(tagger, text))

    @pytest.mark.parametrize(
        ("text", "pieces"),
        [
            ("生徒 立って答えなさい", [("立っ", "動詞"), ("て", "助詞"), ("答え", "動詞")]),
            ("情報 流したのは誰だ", [("流し", "動詞"), ("た", "助動詞")]),
        ],
    )
    def test_a_verb_cut_as_suffix_and_ending_is_cut_again(self, tagger, text, pieces):
        assert _tags(_retag(tagger, text))[1 : 1 + len(pieces)] == pieces

    @pytest.mark.parametrize(
        "text",
        [
            "山田 さんが来た",
            "３ 人で行った",
            "山田 君が来た",
            "鈴木 様がお見えです",
            "山田君が来た",
            "周りをだました",
            "待ってくれ 安さん",
            "こちらは 山田 尊の父です",
            "それ 着な",
            "OK 金は払う",
            "見て 船 船",
            "書類 出しに行く",
        ],
    )
    def test_what_stays(self, tagger, text):
        assert _tags(_retag(tagger, text)) == _tags(list(tagger(text)))

    def test_the_text_and_its_spaces_are_kept(self, tagger):
        text = "生徒 立って答えて 周りを見て"
        out = _retag(tagger, text)
        assert "".join(token.white_space + token.surface for token in out) == text

    def test_after_a_word_the_tagger_does_not_know_nothing_changes(self, tagger):
        tokens = list(tagger("今後 君を呼ぶつもりだ"))
        unknown = morphology.RetaggedToken(tokens[0], {"lemma": None})
        out = morphology.retag_mistagged_tokens([unknown, *tokens[1:]], None, tagger)
        assert _tags(out) == _tags(tokens)

    def test_no_tagger_no_change(self, tagger):
        tokens = list(tagger("彼を利用し 周りをだました"))
        assert _tags(morphology.retag_mistagged_tokens(tokens)) == _tags(tokens)


@needs_tagger
class TestContinuativeBeforeSpace:
    """A noun takes no object: right after を and before a space the stem is the verb."""

    @pytest.mark.parametrize(
        ("text", "stem", "verb"),
        [
            ("戦を嫌い 山に住む", "嫌い", "嫌う"),
            ("神を祭り 村を見守る", "祭り", "祭る"),
            ("親戚を頼り 東京に出た", "頼り", "頼る"),
        ],
    )
    def test_the_stem_is_the_verb(self, tagger, text, stem, verb):
        assert (stem, "名詞") in _tags(list(tagger(text)))
        token = next(token for token in _retag(tagger, text, _lookup) if token.surface == stem)
        assert (token.feature.pos1, token.feature.orthBase) == ("動詞", verb)
        assert token.feature.cForm.startswith("連用形")

    @pytest.mark.parametrize(
        "text",
        [
            "歌を思いきり 歌う",
            "先生をはじめ 全員が来た",
            "村の祭り 楽しみだ",
            "祭りを見た",
        ],
    )
    def test_what_stays(self, tagger, text):
        assert _tags(_retag(tagger, text, _lookup)) == _tags(list(tagger(text)))

    def test_no_dictionary_no_change(self, tagger):
        assert ("嫌い", "名詞") in _tags(_retag(tagger, "戦を嫌い 山に住む"))

    def test_an_unlisted_verb_is_not_taken(self, tagger):
        assert ("嫌い", "名詞") in _tags(_retag(tagger, "戦を嫌い 山に住む", lambda terms: {}))


@needs_tagger
class TestNominalizedStemAfterObject:
    """The masu-stem nominalizer leaves a stem that has an object: it is the verb."""

    @staticmethod
    def _rewrite(tokens: list) -> list:
        return MasuStemNominalizer(lambda terms: set(terms)).rewrite_line(tokens)

    @pytest.mark.parametrize(("text", "stem"), [("身分を偽り 町に入った", "偽り"), ("計画を練り 準備した", "練り")])
    def test_a_stem_after_wo_stays_the_verb(self, tagger, text, stem):
        tokens = list(tagger(text))
        assert (stem, "動詞") in _tags(tokens)
        assert self._rewrite(tokens) is tokens

    def test_a_stem_without_an_object_is_still_nominalized(self, tagger):
        tokens = [token for token in tagger("身分を偽り 町に入った") if token.surface != "を"]
        assert ("偽り", "名詞") in _tags(self._rewrite(tokens))


@needs_tagger
class TestBodyAfterSpacedName:
    """体 that opens a clause is からだ, not the suffix reading たい."""

    @staticmethod
    def _kana(tagger, text: str) -> list[str]:
        tokens = morphology.reread_in_context(list(tagger(text)))
        return [token.feature.kana for token in tokens if token.surface == "体"]

    def test_free_after_a_space_it_is_karada(self, tagger):
        assert [token.feature.kana for token in tagger("田中さん 体は大丈夫？") if token.surface == "体"] == ["タイ"]
        assert self._kana(tagger, "田中さん 体は大丈夫？") == ["カラダ"]

    def test_the_tagger_reading_stays_elsewhere(self, tagger):
        for text in ("先に行け 体が動かない", "体が動かない", "名は体を表す"):
            before = [token.feature.kana for token in tagger(text) if token.surface == "体"]
            assert self._kana(tagger, text) == before, text


@needs_tagger
class TestParser:
    """The corrections reach the cards."""

    def test_a_noun_after_a_space_is_mined(self):
        assert "周り" in _words("彼を利用し 周りをだました")
        assert "金" in _words("あの家 金に困っていない")

    def test_an_honorific_or_a_counter_after_a_space_is_no_card(self):
        assert "さん" not in _words("山田 さんが来た")
        assert "君" not in _words("山田 君が来た")
        assert "人" not in _words("３ 人で行った")

    def test_fixture_records(self):
        records = [json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert records
        for record in records:
            assert _words(record["sentence"]) == set(record["expected"]), record["id"]

"""The zh override tables, checked against jieba's own dictionary and cut.

Every row is measured, not asserted: a retag must leave the cut byte-identical,
and a deleted row must still win the route in a natural frame before the
deletion can reach it. A jieba bump that fixes a row upstream therefore fails
here instead of leaving a dead entry behind.

CC-CEDICT attestation - the third half of the split criterion - has no oracle in
the unit suite; it lives in the ``overrides`` module header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.overrides import ZH_FLAG_OVERRIDES, ZH_SPLIT_ENTRIES
from anki_miner.languages.zh.pos import ZH_ALLOWED_POS, ZH_EXCLUDED_SUBTYPES
from anki_miner.languages.zh.tokenizer import build_tagger
from anki_miner.services.morphology import TokenInclusionRule

jieba = pytest.importorskip("jieba")
posseg = pytest.importorskip("jieba.posseg")

RULE = TokenInclusionRule(allowed_pos=frozenset(ZH_ALLOWED_POS), excluded_subtypes=frozenset(ZH_EXCLUDED_SUBTYPES))

# A retag is checked in frames that put the word where its class belongs; a
# deleted row in frames where jieba's route actually picks it.
T1_FRAMES = ("我{}去。", "他说{}。", "这里有{}。", "{}很好。")
T2_FRAMES = ("我{}。", "他昨天{}了。", "你要不要{}？", "我们一起去{}吧。", "他很喜欢{}。", "别{}了。", "我在{}。")

# The classifier rows do not split head|tail (两本书 cuts to 两本|书), so each
# carries the frame it wins the route in.
CLASSIFIER_FRAMES = {
    "本书": "这是我最喜欢的一本书。",
    "这本": "这本杂志你看完了吗？",
    "两本书": "我在网上买了两本书。",
    "那本书": "那本书是谁的？",
    "几本书": "我在图书馆借了几本书。",
    "整本书": "她一个晚上看完了整本书。",
    "封信": "这封信很长，我还没看完。",
    "一封信": "我妈妈给我写了一封信。",
    "两封信": "他今天收到两封信。",
    "几封信": "他去年一共寄了几封信。",
    "写封信": "你帮我写封信吧。",
    "封信里": "这封信里有一张照片。",
    "十封信": "他一连写了十封信。",
    "封信中": "这封信中提到了你的名字。",
}
VERB_OBJECT = tuple(word for word in ZH_SPLIT_ENTRIES if word not in CLASSIFIER_FRAMES)


@pytest.fixture(scope="module")
def dictionary() -> dict[str, tuple[int, str]]:
    """jieba's shipped ``dict.txt`` as ``word -> (frequency, flag)``."""
    rows = {}
    path = Path(jieba.__file__).resolve().parent / "dict.txt"
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ")
        if len(parts) == 3:
            rows[parts[0]] = (int(parts[1]), parts[2])
    return rows


@pytest.fixture(scope="module")
def stock() -> Any:
    """A jieba tagger with none of the overrides applied."""
    return posseg.POSTokenizer()


@pytest.fixture(scope="module")
def tagger() -> Any:
    return build_tagger()


def _mined(surface: str, flag: str) -> bool:
    return RULE.should_include(
        LanguageToken(surface=surface, pos1=flag[0], pos2="" if len(flag) == 1 else flag, lemma=surface)
    )


def _cut(cutter: Any, text: str) -> list[tuple[str, str]]:
    return [(pair.word, pair.flag) for pair in cutter.cut(text)]


def _tagged(tagger: Any, text: str) -> list[tuple[str, str]]:
    return [(token.surface, token.feature.pos2 or token.feature.pos1) for token in tagger(text)]


class TestFlagOverrides:
    @pytest.mark.parametrize(("word", "new_flag"), sorted(ZH_FLAG_OVERRIDES.items()))
    def test_the_row_it_fixes_is_still_mis_tagged_in_jiebas_dictionary(
        self, word: str, new_flag: str, dictionary: dict[str, tuple[int, str]]
    ) -> None:
        _freq, flag = dictionary[word]
        assert flag != new_flag
        assert not _mined(word, flag)
        assert _mined(word, new_flag)

    @pytest.mark.parametrize("word", sorted(ZH_FLAG_OVERRIDES))
    def test_a_retag_moves_no_token_boundary(self, word: str, stock: Any, tagger: Any) -> None:
        for frame in T1_FRAMES:
            text = frame.format(word)
            before, after = _cut(stock, text), _tagged(tagger, text)
            assert [w for w, _ in before] == [w for w, _ in after], text
            differ = {w for (w, f1), (_w, f2) in zip(before, after, strict=True) if f1 != f2}
            assert differ <= {word}, text

    def test_a_retagged_word_passes_the_pos_gate(self, tagger: Any) -> None:
        """The retag is only worth having if the new class is one the inclusion rule mines."""
        mined = {token.surface for token in tagger("我们一起去喝咖啡。") if RULE.should_include(token)}
        assert {"一起", "喝"} <= mined


class TestSplitEntries:
    @pytest.mark.parametrize("word", sorted(ZH_SPLIT_ENTRIES))
    def test_the_row_it_deletes_is_still_in_jiebas_dictionary(
        self, word: str, dictionary: dict[str, tuple[int, str]]
    ) -> None:
        assert len(word) >= 2
        freq, _flag = dictionary[word]
        assert freq > 0

    @pytest.mark.parametrize("word", VERB_OBJECT)
    def test_a_verb_object_row_fires_and_is_split(self, word: str, stock: Any, tagger: Any) -> None:
        fired = [frame.format(word) for frame in T2_FRAMES if word in [w for w, _ in _cut(stock, frame.format(word))]]
        assert fired, f"{word} never wins the route - the deletion can never reach it"
        for text in fired:
            assert word not in [w for w, _ in _tagged(tagger, text)], text

    @pytest.mark.parametrize(("word", "text"), sorted(CLASSIFIER_FRAMES.items()))
    def test_a_classifier_row_fires_and_is_split(self, word: str, text: str, stock: Any, tagger: Any) -> None:
        assert word in [w for w, _ in _cut(stock, text)], text
        assert word not in [w for w, _ in _tagged(tagger, text)], text

    def test_the_two_rows_the_review_named_give_their_words_back(self, tagger: Any) -> None:
        assert _tagged(tagger, "我每天早上都喝咖啡")[-2:] == [("喝", "v"), ("咖啡", "n")]
        assert [w for w, _ in _tagged(tagger, "他买了三本书")][-2:] == ["三本", "书"]


def test_the_shared_jieba_tagger_keeps_its_own_dictionary(tagger: Any) -> None:
    # The fixture is requested for its side effect, not its value: building the
    # tagger is what runs del_word, and this asserts against the MODULE-level
    # jieba afterwards. Overrides belong to the tagger's private POSTokenizer:
    # another jieba user in the process sees jieba's dictionary as it ships.
    assert posseg.dt.word_tag_tab["一起"] == "m"
    assert ("看电视", "v") in [(pair.word, pair.flag) for pair in posseg.dt.lcut("他看电视")]

"""Retag's key sets follow their tables: an entry added after import is honoured (K1121 PERF).

``retag_mistagged_tokens`` screens tokens with the keys of ``_MISSPLITS``,
``_SPACED_INTERJECTIONS`` and ``_KANA_HOMOPHONE_LEMMAS`` before it looks up a
lemma. The key sets are built on first use; a set frozen at import would skip an
entry added to its table later.
"""

from __future__ import annotations

import fugashi
import pytest

from anki_miner.services import morphology
from anki_miner.services.morphology import retag_mistagged_tokens

_KEY_SETS = (
    morphology._missplit_surfaces,
    morphology._spaced_interjection_surfaces,
    morphology._kana_homophone_orths,
)


@pytest.fixture(autouse=True)
def _fresh_key_sets():
    for key_set in _KEY_SETS:
        key_set.cache_clear()
    yield
    for key_set in _KEY_SETS:
        key_set.cache_clear()


def _tokens(text: str) -> list:
    return list(fugashi.Tagger()(text))


def test_a_missplit_pair_added_after_import_is_recut(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = _tokens("猫がいる")
    first, second = tokens[0], tokens[1]
    assert [t.feature.lemma for t in retag_mistagged_tokens(tokens)[:2]] == [first.feature.lemma, second.feature.lemma]
    key = ((first.surface, first.feature.lemma), (second.surface, second.feature.lemma))
    recut = ((first.surface, {"lemma": "試し一"}), (second.surface, {"lemma": "試し二"}))
    monkeypatch.setitem(morphology._MISSPLITS, key, recut)
    morphology._missplit_surfaces.cache_clear()
    assert [t.feature.lemma for t in retag_mistagged_tokens(tokens)[:2]] == ["試し一", "試し二"]


def test_a_spaced_interjection_added_after_import_is_retagged(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = _tokens("かわいい猫")
    last = tokens[-1]
    assert retag_mistagged_tokens(tokens)[-1].feature.pos1 == last.feature.pos1 != "感動詞"
    monkeypatch.setitem(morphology._SPACED_INTERJECTIONS, (last.surface, last.feature.lemma), last.surface)
    morphology._spaced_interjection_surfaces.cache_clear()
    assert retag_mistagged_tokens(tokens)[-1].feature.pos1 == "感動詞"


def test_a_kana_homophone_added_after_import_takes_its_lemma(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = _tokens("猫がいる")
    verb = tokens[-1]
    assert retag_mistagged_tokens(tokens)[-1].feature.lemma == verb.feature.lemma != "要る"
    monkeypatch.setitem(morphology._KANA_HOMOPHONE_LEMMAS, (verb.feature.orthBase, verb.feature.lemma), "要る")
    morphology._kana_homophone_orths.cache_clear()
    assert retag_mistagged_tokens(tokens)[-1].feature.lemma == "要る"

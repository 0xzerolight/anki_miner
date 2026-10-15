"""ST-07: a frequency source lists its terms in rank order, up to a rank."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.frequency import storage
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from tests.unit.test_indexed_freq_provider import _build_source


def _loaded(db: Path, *, categorical: bool = False) -> IndexedFreqProvider:
    provider = IndexedFreqProvider("list", db, "List", is_categorical=categorical)
    assert provider.load()
    return provider


def test_terms_come_back_in_rank_order_up_to_the_cap(tmp_path):
    provider = _loaded(_build_source(tmp_path, "list", [("犬", "いぬ", 3), ("猫", "ねこ", 1), ("鳥", "とり", 50)]))

    assert provider.terms_by_rank(10) == [("猫", 1), ("犬", 3)]
    provider.close()


def test_a_homograph_counts_once_at_its_best_rank(tmp_path):
    provider = _loaded(_build_source(tmp_path, "list", [("生", "なま", 500), ("生", "せい", 80)]))

    assert provider.terms_by_rank(1000) == [("生", 80)]
    provider.close()


def test_a_categorical_source_has_no_ranks(tmp_path):
    provider = _loaded(
        _build_source(tmp_path, "jlpt", [("猫", None, storage.CATEGORICAL_RANK)]),
        categorical=True,
    )

    assert provider.terms_by_rank(20000) == []
    provider.close()


def test_an_unloaded_provider_lists_nothing(tmp_path):
    assert IndexedFreqProvider("x", tmp_path / "missing" / "index.sqlite", "X").terms_by_rank(10) == []

"""Tests for MultiFrequencyService (additive aggregation across sources)."""

from __future__ import annotations

from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService


class _FakeProvider:
    """Stand-in matching the IndexedFreqProvider surface MultiFrequencyService uses."""

    def __init__(self, name: str, ranks: dict[str, int], *, available: bool = True):
        self._name = name
        self._ranks = ranks
        self._available = available

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def lookup(self, term: str) -> int | None:
        return self._ranks.get(term)


def test_is_available_zero_providers():
    svc = MultiFrequencyService([])
    assert svc.is_available() is False


def test_is_available_one_unavailable():
    svc = MultiFrequencyService([_FakeProvider("A", {}, available=False)])
    assert svc.is_available() is False


def test_is_available_any_available():
    svc = MultiFrequencyService(
        [
            _FakeProvider("A", {}, available=False),
            _FakeProvider("B", {"猫": 1}, available=True),
        ]
    )
    assert svc.is_available() is True


def test_lookup_all_order_and_only_present():
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {"猫": 100}),
            _FakeProvider("BCCWJ", {}),  # no hit -> omitted
            _FakeProvider("Novel", {"猫": 42}),
        ]
    )
    assert svc.lookup_all("猫") == [("JPDB", 100), ("Novel", 42)]


def test_lookup_all_empty_when_no_hits():
    svc = MultiFrequencyService([_FakeProvider("JPDB", {}), _FakeProvider("BCCWJ", {})])
    assert svc.lookup_all("猫") == []


def test_lookup_all_no_providers():
    assert MultiFrequencyService([]).lookup_all("猫") == []


def test_lookup_min_picks_smallest():
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {"猫": 100}),
            _FakeProvider("Novel", {"猫": 42}),
            _FakeProvider("BCCWJ", {"猫": 300}),
        ]
    )
    assert svc.lookup_min("猫") == 42


def test_lookup_min_single_hit():
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {}),
            _FakeProvider("Novel", {"猫": 42}),
        ]
    )
    assert svc.lookup_min("猫") == 42


def test_lookup_min_none_when_no_hits():
    svc = MultiFrequencyService([_FakeProvider("JPDB", {}), _FakeProvider("BCCWJ", {})])
    assert svc.lookup_min("猫") is None


def test_lookup_min_none_no_providers():
    assert MultiFrequencyService([]).lookup_min("猫") is None

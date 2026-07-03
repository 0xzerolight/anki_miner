"""Tests for MultiFrequencyService (additive aggregation across sources)."""

from __future__ import annotations

from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService


class _FakeProvider:
    """Stand-in matching the IndexedFreqProvider surface MultiFrequencyService uses."""

    def __init__(
        self,
        name: str,
        ranks: dict[str, int],
        *,
        available: bool = True,
        displays: dict[str, str] | None = None,
    ):
        self._name = name
        self._ranks = ranks
        self._displays = displays or {}
        self._available = available
        self.close_calls = 0
        self.last_reading: str | None = None

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def lookup_detail(self, term: str, reading: str | None = None) -> tuple[int, str | None] | None:
        self.last_reading = reading
        rank = self._ranks.get(term)
        if rank is None:
            return None
        return rank, self._displays.get(term)

    def close(self) -> None:
        self.close_calls += 1


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
    assert svc.lookup_all("猫") == [("JPDB", 100, None), ("Novel", 42, None)]


def test_lookup_all_carries_display_value():
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {"猫": 1099}, displays={"猫": "1099/72000"}),
            _FakeProvider("BCCWJ", {"猫": 42}),  # no display -> None
        ]
    )
    assert svc.lookup_all("猫") == [("JPDB", 1099, "1099/72000"), ("BCCWJ", 42, None)]


def test_lookup_all_empty_when_no_hits():
    svc = MultiFrequencyService([_FakeProvider("JPDB", {}), _FakeProvider("BCCWJ", {})])
    assert svc.lookup_all("猫") == []


def test_lookup_all_no_providers():
    assert MultiFrequencyService([]).lookup_all("猫") == []


def test_reading_threaded_to_each_provider():
    a = _FakeProvider("A", {"猫": 100})
    b = _FakeProvider("B", {"猫": 42})
    svc = MultiFrequencyService([a, b])
    svc.lookup_all("猫", "ねこ")
    assert a.last_reading == "ねこ"
    assert b.last_reading == "ねこ"


def test_reading_threaded_through_min_and_harmonic():
    a = _FakeProvider("A", {"猫": 100})
    svc = MultiFrequencyService([a])
    svc.lookup_min("猫", "ねこ")
    assert a.last_reading == "ねこ"
    svc.lookup_harmonic("猫", "みゃー")
    assert a.last_reading == "みゃー"


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


def test_lookup_harmonic_multiple_sources():
    # floor(3 / (1/100 + 1/42 + 1/300)) = floor(80.77) = 80. The harmonic mean
    # keeps one niche source from dominating the way a bare MIN would.
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {"猫": 100}),
            _FakeProvider("Novel", {"猫": 42}),
            _FakeProvider("BCCWJ", {"猫": 300}),
        ]
    )
    assert svc.lookup_harmonic("猫") == 80


def test_lookup_harmonic_two_sources():
    # floor(2 / (1/500 + 1/612)) = floor(550.36) = 550.
    svc = MultiFrequencyService(
        [
            _FakeProvider("BCCWJ", {"猫": 500}),
            _FakeProvider("JPDB", {"猫": 612}),
        ]
    )
    assert svc.lookup_harmonic("猫") == 550


def test_lookup_harmonic_single_hit_equals_rank():
    # With one source the harmonic mean collapses to that source's rank.
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {}),
            _FakeProvider("Novel", {"猫": 42}),
        ]
    )
    assert svc.lookup_harmonic("猫") == 42


def test_lookup_harmonic_none_when_no_hits():
    svc = MultiFrequencyService([_FakeProvider("JPDB", {}), _FakeProvider("BCCWJ", {})])
    assert svc.lookup_harmonic("猫") is None


def test_lookup_harmonic_none_no_providers():
    assert MultiFrequencyService([]).lookup_harmonic("猫") is None


def test_lookup_harmonic_ignores_nonpositive_rank():
    # Yomitan's getFrequencyNumbers drops frequencies <= 0; mirror that so a bad
    # source rank of 0 neither divides-by-zero nor skews the mean.
    svc = MultiFrequencyService(
        [
            _FakeProvider("Bad", {"猫": 0}),
            _FakeProvider("Novel", {"猫": 50}),
        ]
    )
    assert svc.lookup_harmonic("猫") == 50


def test_close_closes_each_provider_once():
    a = _FakeProvider("A", {})
    b = _FakeProvider("B", {})
    MultiFrequencyService([a, b]).close()
    assert a.close_calls == 1
    assert b.close_calls == 1


def test_close_is_idempotent():
    a = _FakeProvider("A", {})
    svc = MultiFrequencyService([a])
    svc.close()
    svc.close()
    # Each call delegates to the provider, which is itself idempotent;
    # the service must tolerate being closed twice without raising.
    assert a.close_calls == 2


def test_close_no_providers_is_safe():
    MultiFrequencyService([]).close()  # must not raise


def test_close_tolerates_provider_without_close():
    class _NoClose:
        @property
        def name(self) -> str:
            return "N"

        def is_available(self) -> bool:
            return True

        def lookup(self, term: str) -> int | None:
            return None

    MultiFrequencyService([_NoClose()]).close()  # must not raise


def test_close_suppresses_provider_close_exception():
    class _Boom(_FakeProvider):
        def close(self) -> None:
            raise RuntimeError("boom")

    good = _FakeProvider("good", {})
    # A raising provider must not stop the others from closing.
    MultiFrequencyService([_Boom("boom", {}), good]).close()
    assert good.close_calls == 1

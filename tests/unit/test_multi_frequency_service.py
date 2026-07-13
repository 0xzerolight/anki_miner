"""Tests for MultiFrequencyService (additive aggregation across sources)."""

from __future__ import annotations

from anki_miner.services.frequency.multi_frequency_service import (
    MultiFrequencyService,
    harmonic_rank,
    min_rank,
)
from anki_miner.services.frequency.storage import CATEGORICAL_RANK


class _FakeProvider:
    """Stand-in matching the IndexedFreqProvider surface MultiFrequencyService uses."""

    def __init__(
        self,
        name: str,
        ranks: dict[str, int],
        *,
        available: bool = True,
        displays: dict[str, str] | None = None,
        is_categorical: bool = False,
    ):
        self._name = name
        self._ranks = ranks
        self._displays = displays or {}
        self._available = available
        self.is_categorical = is_categorical
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


# min_rank/harmonic_rank reduce a fetched lookup_all list; the tests below drive
# them through the live production path (service.lookup_all -> pure helper), the
# same single-fetch form EpisodeProcessor uses.


def test_min_rank_over_service_picks_smallest():
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {"猫": 100}),
            _FakeProvider("Novel", {"猫": 42}),
            _FakeProvider("BCCWJ", {"猫": 300}),
        ]
    )
    assert min_rank(svc.lookup_all("猫")) == 42


def test_min_rank_over_service_single_hit():
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {}),
            _FakeProvider("Novel", {"猫": 42}),
        ]
    )
    assert min_rank(svc.lookup_all("猫")) == 42


def test_min_rank_over_service_none_when_no_hits():
    svc = MultiFrequencyService([_FakeProvider("JPDB", {}), _FakeProvider("BCCWJ", {})])
    assert min_rank(svc.lookup_all("猫")) is None


def test_min_rank_over_service_none_no_providers():
    assert min_rank(MultiFrequencyService([]).lookup_all("猫")) is None


def test_harmonic_rank_over_service_multiple_sources():
    # floor(3 / (1/100 + 1/42 + 1/300)) = floor(80.77) = 80. The harmonic mean
    # keeps one niche source from dominating the way a bare MIN would.
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {"猫": 100}),
            _FakeProvider("Novel", {"猫": 42}),
            _FakeProvider("BCCWJ", {"猫": 300}),
        ]
    )
    assert harmonic_rank(svc.lookup_all("猫")) == 80


def test_harmonic_rank_over_service_two_sources():
    # floor(2 / (1/500 + 1/612)) = floor(550.36) = 550.
    svc = MultiFrequencyService(
        [
            _FakeProvider("BCCWJ", {"猫": 500}),
            _FakeProvider("JPDB", {"猫": 612}),
        ]
    )
    assert harmonic_rank(svc.lookup_all("猫")) == 550


def test_harmonic_rank_over_service_single_hit_equals_rank():
    # With one source the harmonic mean collapses to that source's rank.
    svc = MultiFrequencyService(
        [
            _FakeProvider("JPDB", {}),
            _FakeProvider("Novel", {"猫": 42}),
        ]
    )
    assert harmonic_rank(svc.lookup_all("猫")) == 42


def test_harmonic_rank_over_service_none_when_no_hits():
    svc = MultiFrequencyService([_FakeProvider("JPDB", {}), _FakeProvider("BCCWJ", {})])
    assert harmonic_rank(svc.lookup_all("猫")) is None


def test_harmonic_rank_over_service_none_no_providers():
    assert harmonic_rank(MultiFrequencyService([]).lookup_all("猫")) is None


def test_harmonic_rank_over_service_ignores_nonpositive_rank():
    # Yomitan's getFrequencyNumbers drops frequencies <= 0; mirror that so a bad
    # source rank of 0 neither divides-by-zero nor skews the mean.
    svc = MultiFrequencyService(
        [
            _FakeProvider("Bad", {"猫": 0}),
            _FakeProvider("Novel", {"猫": 50}),
        ]
    )
    assert harmonic_rank(svc.lookup_all("猫")) == 50


class TestPureRankHelpers:
    """min_rank/harmonic_rank derive min + harmonic from an already-fetched
    lookup_all list, so a caller need not re-run the per-source SQL three times."""

    def test_min_rank_empty(self):
        assert min_rank([]) is None

    def test_min_rank_picks_smallest(self):
        assert min_rank([("A", 100, None), ("B", 42, None), ("C", 300, None)]) == 42

    def test_harmonic_rank_empty(self):
        assert harmonic_rank([]) is None

    def test_harmonic_rank_multiple_sources(self):
        # floor(3 / (1/100 + 1/42 + 1/300)) = 80, matching the service method.
        assert harmonic_rank([("A", 100, None), ("B", 42, None), ("C", 300, None)]) == 80

    def test_harmonic_rank_ignores_nonpositive(self):
        assert harmonic_rank([("Bad", 0, None), ("Novel", 50, None)]) == 50

    def test_harmonic_rank_single_source_exact(self):
        # Regression (2026-07 card audit): float 1/(1/f) truncated to f-1 for
        # ~8% of ranks — 29814 rendered "Frequency: 29814" but sorted as 29813.
        # Exact rational arithmetic must return the rank itself for one source.
        for rank in (29814, 16312, 7918, 27838, 23346, 14932, 42, 1):
            assert harmonic_rank([("A", rank, None)]) == rank

    def test_harmonic_rank_multi_source_exact_floor(self):
        # Equal ranks: harmonic mean of (N, N) is exactly N — the float path
        # could floor this to N-1 as well. An inexact case still floors.
        assert harmonic_rank([("A", 29814, None), ("B", 29814, None)]) == 29814
        # floor(2 / (1/3 + 1/7)) = floor(4.2) = 4
        assert harmonic_rank([("A", 3, None), ("B", 7, None)]) == 4


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


class TestCategoricalSentinelExclusion:
    """Word-based (categorical) sources carry the CATEGORICAL_RANK sentinel; the
    aggregation helpers exclude it while the per-source breakdown keeps its label."""

    def test_min_rank_excludes_sentinel(self) -> None:
        assert min_rank([("Freq", 45000, None), ("JLPT", CATEGORICAL_RANK, "N5")]) == 45000
        # A word ranked ONLY by categorical sources has no numeric rank.
        assert min_rank([("JLPT", CATEGORICAL_RANK, "N5")]) is None

    def test_harmonic_rank_excludes_sentinel_no_phantom_n(self) -> None:
        # Without exclusion the phantom second source would inflate n:
        # floor(2 / (1/45000 + ~0)) == 90000. Excluded -> floor(1/(1/45000)) == 45000.
        assert harmonic_rank([("Freq", 45000, None), ("JLPT", CATEGORICAL_RANK, "N5")]) == 45000
        assert harmonic_rank([("JLPT", CATEGORICAL_RANK, "N5")]) is None

    def test_service_categorical_in_lookup_all_but_excluded_from_scalars(self) -> None:
        numeric = _FakeProvider("Freq", {"猫": 45000})
        categorical = _FakeProvider("JLPT", {"猫": CATEGORICAL_RANK}, displays={"猫": "N5"})
        svc = MultiFrequencyService([numeric, categorical])
        # Both sources appear in the card breakdown, categorical carrying its label.
        sources = svc.lookup_all("猫")
        assert sources == [("Freq", 45000, None), ("JLPT", CATEGORICAL_RANK, "N5")]
        # But the scalar rank/sort come from the numeric source only.
        assert min_rank(sources) == 45000
        assert harmonic_rank(sources) == 45000


class TestHasNumericSource:
    """has_numeric_source() drives the max_frequency_rank cutoff gate: a chain of
    ONLY categorical sources yields no numeric rank, so the cutoff must stay inert."""

    def test_no_providers(self) -> None:
        assert MultiFrequencyService([]).has_numeric_source() is False

    def test_numeric_source_present(self) -> None:
        svc = MultiFrequencyService([_FakeProvider("Freq", {"猫": 1})])
        assert svc.has_numeric_source() is True

    def test_categorical_only_is_false(self) -> None:
        svc = MultiFrequencyService([_FakeProvider("JLPT", {"猫": CATEGORICAL_RANK}, is_categorical=True)])
        assert svc.has_numeric_source() is False

    def test_mixed_chain_is_true(self) -> None:
        svc = MultiFrequencyService(
            [
                _FakeProvider("JLPT", {"猫": CATEGORICAL_RANK}, is_categorical=True),
                _FakeProvider("Freq", {"猫": 1}),
            ]
        )
        assert svc.has_numeric_source() is True

    def test_unavailable_numeric_source_ignored(self) -> None:
        # A numeric source that failed to load must not count as usable.
        svc = MultiFrequencyService([_FakeProvider("Freq", {"猫": 1}, available=False)])
        assert svc.has_numeric_source() is False

"""Tests for the frequency direction probe (rank- vs occurrence-based detection)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe
from anki_miner.services.frequency.mode_probe import (
    LESS_COMMON_TERMS,
    MORE_COMMON_TERMS,
    convert_to_ranks,
    probe_direction,
    resolve_is_occurrence,
)


def _occurrence_lookup(term: str) -> list[int]:
    """Common terms carry big counts, rare terms small ones (occurrence-based)."""
    if term in MORE_COMMON_TERMS["ja"]:
        return [1000]
    if term in LESS_COMMON_TERMS["ja"]:
        return [5]
    return []


def _rank_lookup(term: str) -> list[int]:
    """Common terms carry small ranks, rare terms big ones (rank-based)."""
    if term in MORE_COMMON_TERMS["ja"]:
        return [10]
    if term in LESS_COMMON_TERMS["ja"]:
        return [9000]
    return []


class TestProbeDirection:
    def test_occurrence_shape_is_descending(self) -> None:
        assert probe_direction(_occurrence_lookup) == mode_probe.DESCENDING

    def test_rank_shape_is_ascending(self) -> None:
        assert probe_direction(_rank_lookup) == mode_probe.ASCENDING

    def test_no_probe_terms_is_ambiguous(self) -> None:
        assert probe_direction(lambda _term: []) is None

    def test_partial_coverage_single_pair_votes(self) -> None:
        # Only one common + one rare term present; still enough to vote.
        def lookup(term: str) -> list[int]:
            if term == MORE_COMMON_TERMS["ja"][0]:
                return [900]
            if term == LESS_COMMON_TERMS["ja"][0]:
                return [3]
            return []

        assert probe_direction(lookup) == mode_probe.DESCENDING

    def test_unknown_language_pools_all_terms(self) -> None:
        # With an unknown language the ja terms are still pooled in, so the
        # occurrence shape is detected.
        assert probe_direction(_occurrence_lookup, source_language="xx") == mode_probe.DESCENDING


class TestResolveIsOccurrence:
    def test_declared_occurrence_authoritative(self) -> None:
        # Declared occurrence wins even when values look rank-shaped.
        assert resolve_is_occurrence(mode_probe.OCCURRENCE_BASED, {}) is True

    def test_declared_rank_authoritative(self) -> None:
        # Declared rank wins even against occurrence-shaped values.
        values = {t: [1000] for t in MORE_COMMON_TERMS["ja"]}
        values.update({t: [5] for t in LESS_COMMON_TERMS["ja"]})
        assert resolve_is_occurrence(mode_probe.RANK_BASED, values) is False

    def test_undeclared_probes_occurrence(self) -> None:
        values = {t: [1000] for t in MORE_COMMON_TERMS["ja"]}
        values.update({t: [5] for t in LESS_COMMON_TERMS["ja"]})
        assert resolve_is_occurrence("", values) is True

    def test_undeclared_probes_rank(self) -> None:
        values = {t: [10] for t in MORE_COMMON_TERMS["ja"]}
        values.update({t: [9000] for t in LESS_COMMON_TERMS["ja"]})
        assert resolve_is_occurrence("", values) is False

    def test_undeclared_ambiguous_defaults_rank(self) -> None:
        assert resolve_is_occurrence("", {"猫": [5]}) is False


class TestConvertToRanks:
    def test_reranks_descending_to_1_n(self) -> None:
        rows = [("猫", None, 5, None), ("犬", None, 100, None), ("鳥", None, 20, None)]
        assert convert_to_ranks(rows) == [
            ("犬", None, 1, None),
            ("鳥", None, 2, None),
            ("猫", None, 3, None),
        ]

    def test_preserves_display_value(self) -> None:
        rows = [("猫", None, 5, "5回"), ("犬", None, 100, "100回")]
        assert convert_to_ranks(rows) == [("犬", None, 1, "100回"), ("猫", None, 2, "5回")]

    def test_ties_break_deterministically(self) -> None:
        rows = [("鳥", None, 7, None), ("猫", None, 7, None), ("犬", None, 7, None)]
        # Equal values → ordered by term, ranks 1..n.
        assert convert_to_ranks(rows) == [
            ("犬", None, 1, None),
            ("猫", None, 2, None),
            ("鳥", None, 3, None),
        ]

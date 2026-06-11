"""Tests for corpus_aggregator: aggregate() and select()."""

from __future__ import annotations

import collections
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models.deck_build import DeckSelectionMode
from anki_miner.services.corpus_aggregator import aggregate, select
from anki_miner.utils.file_pairing import FilePair

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pair(name: str) -> FilePair:
    """Build a FilePair with dummy Paths for testing."""
    return FilePair(video=Path(f"/fake/{name}.mp4"), subtitle=Path(f"/fake/{name}.ass"))


def _parser(mapping: dict[Path, collections.Counter[str]]) -> MagicMock:
    """Return a mock parser whose count_lemmas returns canned Counters."""
    parser = MagicMock()
    parser.count_lemmas.side_effect = lambda p: mapping.get(p, collections.Counter())
    return parser


# ---------------------------------------------------------------------------
# aggregate()
# ---------------------------------------------------------------------------


class TestAggregate:
    """Tests for corpus_aggregator.aggregate."""

    def test_single_pair_returns_its_counts(self):
        pair = _pair("ep01")
        counts = collections.Counter({"食べる": 3, "行く": 2})
        parser = _parser({pair.subtitle: counts})

        result = aggregate(parser, [pair])

        assert result == counts

    def test_two_pairs_sum_disjoint_lemmas(self):
        p1 = _pair("ep01")
        p2 = _pair("ep02")
        counts1 = collections.Counter({"食べる": 3, "行く": 2})
        counts2 = collections.Counter({"見る": 5, "来る": 1})
        parser = _parser({p1.subtitle: counts1, p2.subtitle: counts2})

        result = aggregate(parser, [p1, p2])

        assert result == collections.Counter({"食べる": 3, "行く": 2, "見る": 5, "来る": 1})

    def test_two_pairs_sum_overlapping_lemmas(self):
        p1 = _pair("ep01")
        p2 = _pair("ep02")
        counts1 = collections.Counter({"食べる": 3, "行く": 2})
        counts2 = collections.Counter({"食べる": 4, "見る": 1})
        parser = _parser({p1.subtitle: counts1, p2.subtitle: counts2})

        result = aggregate(parser, [p1, p2])

        assert result["食べる"] == 7
        assert result["行く"] == 2
        assert result["見る"] == 1

    def test_three_pairs_all_overlapping(self):
        p1, p2, p3 = _pair("ep01"), _pair("ep02"), _pair("ep03")
        parser = _parser(
            {
                p1.subtitle: collections.Counter({"a": 10}),
                p2.subtitle: collections.Counter({"a": 5, "b": 3}),
                p3.subtitle: collections.Counter({"b": 2, "c": 1}),
            }
        )

        result = aggregate(parser, [p1, p2, p3])

        assert result == collections.Counter({"a": 15, "b": 5, "c": 1})

    def test_empty_pair_list_returns_empty_counter(self):
        parser = _parser({})
        result = aggregate(parser, [])
        assert result == collections.Counter()
        assert isinstance(result, collections.Counter)

    def test_parser_called_with_subtitle_path_for_each_pair(self):
        p1 = _pair("ep01")
        p2 = _pair("ep02")
        parser = _parser({p1.subtitle: collections.Counter({"x": 1}), p2.subtitle: collections.Counter({"y": 2})})

        aggregate(parser, [p1, p2])

        assert parser.count_lemmas.call_count == 2
        called_paths = {call.args[0] for call in parser.count_lemmas.call_args_list}
        assert called_paths == {p1.subtitle, p2.subtitle}

    def test_cancel_check_stops_between_files(self):
        """Regression (T-25a): cancel_check=True stops the per-file loop early.

        aggregate() is the longest deck-builder Phase-1 step (MeCab over the
        whole corpus, minutes); the optional callback lets the worker abort
        between files instead of grinding through every remaining subtitle.
        """
        p1, p2, p3 = _pair("ep01"), _pair("ep02"), _pair("ep03")
        parser = _parser(
            {
                p1.subtitle: collections.Counter({"a": 1}),
                p2.subtitle: collections.Counter({"b": 1}),
                p3.subtitle: collections.Counter({"c": 1}),
            }
        )
        cancel_check = MagicMock(side_effect=[False, True])

        result = aggregate(parser, [p1, p2, p3], cancel_check=cancel_check)

        # First file processed; loop stopped before the second.
        assert parser.count_lemmas.call_count == 1
        assert result == collections.Counter({"a": 1})

    def test_cancel_check_false_processes_everything(self):
        """A cancel_check that never fires must not change the aggregate result."""
        p1, p2 = _pair("ep01"), _pair("ep02")
        parser = _parser({p1.subtitle: collections.Counter({"a": 1}), p2.subtitle: collections.Counter({"b": 2})})

        result = aggregate(parser, [p1, p2], cancel_check=lambda: False)

        assert result == collections.Counter({"a": 1, "b": 2})


# ---------------------------------------------------------------------------
# select() — ALL mode
# ---------------------------------------------------------------------------


class TestSelectAll:
    """Tests for select() with DeckSelectionMode.ALL."""

    def test_all_mode_returns_every_lemma(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3, "c": 1})
        candidates, preview = select(counts, DeckSelectionMode.ALL, 0.0, set())

        assert candidates == {"a", "b", "c"}

    def test_all_mode_preview_fields(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3, "c": 1})
        candidates, preview = select(counts, DeckSelectionMode.ALL, 0.0, set())

        assert preview.total_tokens == 9
        assert preview.unique_lemmas == 3
        assert preview.candidate_count == 3
        assert preview.projected_coverage_pct == pytest.approx(100.0)
        assert preview.known_skipped == 0
        assert preview.card_count == 3

    def test_all_mode_with_known_lemmas(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3, "c": 1})
        candidates, preview = select(counts, DeckSelectionMode.ALL, 0.0, {"a", "c"})

        # Candidate set is NOT filtered by known_lemmas
        assert candidates == {"a", "b", "c"}
        assert preview.known_skipped == 2
        assert preview.card_count == 1

    def test_all_mode_empty_known_set_gives_full_card_count(self):
        counts: collections.Counter[str] = collections.Counter({"x": 10, "y": 4})
        candidates, preview = select(counts, DeckSelectionMode.ALL, 0.0, set())

        assert preview.known_skipped == 0
        assert preview.card_count == 2


# ---------------------------------------------------------------------------
# select() — TOP_N mode
# ---------------------------------------------------------------------------


class TestSelectTopN:
    """Tests for select() with DeckSelectionMode.TOP_N."""

    def test_top_n_selects_highest_frequency_lemmas(self):
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates, preview = select(counts, DeckSelectionMode.TOP_N, 2.0, set())

        assert candidates == {"a", "b"}

    def test_top_n_preview_coverage(self):
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates, preview = select(counts, DeckSelectionMode.TOP_N, 2.0, set())

        assert preview.total_tokens == 100
        assert preview.candidate_count == 2
        # (50+30)/100 = 80%
        assert preview.projected_coverage_pct == pytest.approx(80.0)

    def test_top_n_value_exceeds_unique_returns_all(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3})
        candidates, preview = select(counts, DeckSelectionMode.TOP_N, 100.0, set())

        assert candidates == {"a", "b"}
        assert preview.candidate_count == 2

    def test_top_n_value_zero_returns_empty(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3})
        candidates, preview = select(counts, DeckSelectionMode.TOP_N, 0.0, set())

        assert candidates == set()
        assert preview.candidate_count == 0
        assert preview.card_count == 0

    def test_top_n_negative_value_returns_empty(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3})
        candidates, preview = select(counts, DeckSelectionMode.TOP_N, -5.0, set())

        assert candidates == set()

    def test_top_n_exactly_unique_count_returns_all(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3, "c": 1})
        candidates, preview = select(counts, DeckSelectionMode.TOP_N, 3.0, set())

        assert candidates == {"a", "b", "c"}

    def test_top_n_with_known_lemmas_does_not_shrink_candidate_set(self):
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates_no_known, _ = select(counts, DeckSelectionMode.TOP_N, 3.0, set())
        candidates_with_known, preview = select(counts, DeckSelectionMode.TOP_N, 3.0, {"a"})

        assert candidates_no_known == candidates_with_known
        assert preview.known_skipped == 1
        assert preview.card_count == 2


# ---------------------------------------------------------------------------
# select() — COVERAGE_PCT mode
# ---------------------------------------------------------------------------


class TestSelectCoveragePct:
    """Tests for select() with DeckSelectionMode.COVERAGE_PCT."""

    def test_coverage_80_selects_minimal_prefix(self):
        # {a:50, b:30, c:15, d:5} total=100; 50+30=80 >= 80%
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 80.0, set())

        assert candidates == {"a", "b"}

    def test_coverage_80_preview_fields(self):
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 80.0, set())

        assert preview.total_tokens == 100
        assert preview.candidate_count == 2
        assert preview.projected_coverage_pct == pytest.approx(80.0)

    def test_coverage_50_selects_just_top_word(self):
        # {a:50, b:30, c:15, d:5} total=100; 50 >= 50%
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 50.0, set())

        assert candidates == {"a"}
        assert preview.candidate_count == 1

    def test_coverage_100_returns_all(self):
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 100.0, set())

        assert candidates == {"a", "b", "c", "d"}

    def test_coverage_single_word_exceeds_target(self):
        # a=90 covers 90% on its own; target 10% → just {a}
        counts: collections.Counter[str] = collections.Counter({"a": 90, "b": 10})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 10.0, set())

        assert candidates == {"a"}
        assert preview.projected_coverage_pct == pytest.approx(90.0)

    def test_coverage_zero_selects_nothing(self):
        # 0% target is degenerate but must not select a lemma (no implicit first word).
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 20})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 0.0, set())

        assert candidates == set()
        assert preview.candidate_count == 0
        assert preview.projected_coverage_pct == pytest.approx(0.0)
        assert preview.card_count == 0

    def test_coverage_just_under_threshold_adds_next(self):
        # a=79, b=21 total=100; target 80%; after a: 79% < 80 → add b → 100%
        counts: collections.Counter[str] = collections.Counter({"a": 79, "b": 21})
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 80.0, set())

        assert candidates == {"a", "b"}
        assert preview.projected_coverage_pct == pytest.approx(100.0)

    def test_coverage_with_known_lemmas_does_not_change_candidate_set(self):
        counts: collections.Counter[str] = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        candidates_no_known, _ = select(counts, DeckSelectionMode.COVERAGE_PCT, 80.0, set())
        candidates_with_known, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 80.0, {"a"})

        assert candidates_no_known == candidates_with_known
        assert preview.known_skipped == 1
        assert preview.card_count == 1


# ---------------------------------------------------------------------------
# select() — tie-breaking (first-occurrence order)
# ---------------------------------------------------------------------------


class TestSelectTieBreaking:
    """Tests that equal-count lemmas resolve by first-occurrence (insertion) order."""

    def test_tied_lemmas_ordered_by_first_occurrence(self):
        # Both "b" and "c" have count=5; "b" was inserted first.
        # TOP_N=1 should pick "b" (first encountered among ties after "a").
        counts: collections.Counter[str] = collections.Counter()
        counts["a"] = 10
        counts["b"] = 5
        counts["c"] = 5
        candidates, _ = select(counts, DeckSelectionMode.TOP_N, 2.0, set())

        # "a" is top, then "b" ties "c" but was inserted first
        assert candidates == {"a", "b"}

    def test_tied_lemmas_coverage_mode_uses_first_occurrence(self):
        # a=40, b=30, c=30 total=100; target 70%: after a (40%) < 70; after a+b (70%) >= 70
        # b comes before c by insertion order
        counts: collections.Counter[str] = collections.Counter()
        counts["a"] = 40
        counts["b"] = 30
        counts["c"] = 30
        candidates, preview = select(counts, DeckSelectionMode.COVERAGE_PCT, 70.0, set())

        assert candidates == {"a", "b"}
        assert preview.projected_coverage_pct == pytest.approx(70.0)


# ---------------------------------------------------------------------------
# select() — empty corpus
# ---------------------------------------------------------------------------


class TestSelectEmptyCorpus:
    """Tests for select() with an empty corpus."""

    @pytest.mark.parametrize("mode", list(DeckSelectionMode))
    def test_empty_corpus_returns_empty_candidates(self, mode: DeckSelectionMode):
        candidates, preview = select(collections.Counter(), mode, 80.0, set())
        assert candidates == set()

    @pytest.mark.parametrize("mode", list(DeckSelectionMode))
    def test_empty_corpus_preview_all_zeros(self, mode: DeckSelectionMode):
        _, preview = select(collections.Counter(), mode, 80.0, set())
        assert preview.total_tokens == 0
        assert preview.unique_lemmas == 0
        assert preview.candidate_count == 0
        assert preview.projected_coverage_pct == 0.0
        assert preview.known_skipped == 0
        assert preview.card_count == 0

    @pytest.mark.parametrize("mode", list(DeckSelectionMode))
    def test_empty_corpus_no_zero_division(self, mode: DeckSelectionMode):
        # Must not raise ZeroDivisionError
        _, preview = select(collections.Counter(), mode, 50.0, {"any_word"})
        assert preview.projected_coverage_pct == 0.0


# ---------------------------------------------------------------------------
# select() — known_skipped / card_count contract
# ---------------------------------------------------------------------------


class TestSelectKnownSkipped:
    """Tests verifying known_skipped and card_count semantics."""

    def test_empty_known_set_zero_skipped(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3})
        _, preview = select(counts, DeckSelectionMode.ALL, 0.0, set())
        assert preview.known_skipped == 0
        assert preview.card_count == preview.candidate_count

    def test_all_known_zero_cards(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3})
        candidates, preview = select(counts, DeckSelectionMode.ALL, 0.0, {"a", "b"})
        assert candidates == {"a", "b"}  # candidate_set unchanged
        assert preview.known_skipped == 2
        assert preview.card_count == 0

    def test_partial_known_correct_counts(self):
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3, "c": 1})
        candidates, preview = select(counts, DeckSelectionMode.ALL, 0.0, {"b"})
        assert "b" in candidates  # still in set
        assert preview.known_skipped == 1
        assert preview.card_count == 2

    def test_known_not_in_candidate_set_ignored(self):
        # known_lemmas includes "z" which is not in counts at all
        counts: collections.Counter[str] = collections.Counter({"a": 5, "b": 3})
        _, preview = select(counts, DeckSelectionMode.ALL, 0.0, {"z"})
        assert preview.known_skipped == 0
        assert preview.card_count == 2

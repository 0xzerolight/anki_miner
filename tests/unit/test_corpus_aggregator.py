"""Tests for corpus_aggregator: aggregate(), rank_select(), row_lemmas(), build_preview()."""

import collections
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models.deck_build import DeckCorpus, DeckSelectionMode
from anki_miner.models.word import TokenizedWord
from anki_miner.services.corpus_aggregator import aggregate, build_preview, rank_select, row_lemmas

# ---------------------------------------------------------------------------
# aggregate()
# ---------------------------------------------------------------------------


class TestAggregate:
    """Tests for corpus_aggregator.aggregate."""

    def test_single_subtitle_returns_its_counts(self):
        subtitle = Path("/fake/ep01.ass")
        counts = collections.Counter({"食べる": 3, "行く": 2})
        parser = MagicMock()
        parser.count_lemmas.side_effect = lambda p: counts if p == subtitle else collections.Counter()

        result = aggregate(parser, [subtitle])

        assert result == counts

    def test_two_subtitles_sum_disjoint_lemmas(self):
        s1, s2 = Path("/fake/ep01.ass"), Path("/fake/ep02.ass")
        mapping = {
            s1: collections.Counter({"食べる": 3, "行く": 2}),
            s2: collections.Counter({"見る": 5, "来る": 1}),
        }
        parser = MagicMock()
        parser.count_lemmas.side_effect = lambda p: mapping[p]

        result = aggregate(parser, [s1, s2])

        assert result == collections.Counter({"食べる": 3, "行く": 2, "見る": 5, "来る": 1})

    def test_two_subtitles_sum_overlapping_lemmas(self):
        s1, s2 = Path("/fake/ep01.ass"), Path("/fake/ep02.ass")
        mapping = {
            s1: collections.Counter({"食べる": 3, "行く": 2}),
            s2: collections.Counter({"食べる": 4, "見る": 1}),
        }
        parser = MagicMock()
        parser.count_lemmas.side_effect = lambda p: mapping[p]

        result = aggregate(parser, [s1, s2])

        assert result == collections.Counter({"食べる": 7, "行く": 2, "見る": 1})

    def test_empty_subtitle_list_returns_empty_counter(self):
        parser = MagicMock()

        result = aggregate(parser, [])

        assert result == collections.Counter()
        assert isinstance(result, collections.Counter)
        parser.count_lemmas.assert_not_called()

    def test_cancel_check_false_processes_everything(self):
        s1, s2 = Path("ep01.ass"), Path("ep02.ass")
        mapping = {s1: collections.Counter({"a": 1}), s2: collections.Counter({"b": 2})}
        parser = MagicMock()
        parser.count_lemmas.side_effect = lambda p: mapping[p]

        result = aggregate(parser, [s1, s2], cancel_check=lambda: False)

        assert result == collections.Counter({"a": 1, "b": 2})


def test_aggregate_polls_cancel_between_files():
    parser = MagicMock()
    parser.count_lemmas.side_effect = [collections.Counter({"a": 1}), collections.Counter({"b": 1})]
    calls = iter([False, True])
    result = aggregate(parser, [Path("1.ass"), Path("2.ass")], cancel_check=lambda: next(calls))
    assert result == collections.Counter({"a": 1})
    assert parser.count_lemmas.call_count == 1


# ---------------------------------------------------------------------------
# rank_select() — ALL mode
# ---------------------------------------------------------------------------


class TestRankSelectAll:
    """Tests for rank_select() with DeckSelectionMode.ALL."""

    def test_all_mode_returns_every_lemma(self):
        counts = collections.Counter({"a": 5, "b": 3, "c": 1})
        assert rank_select(counts, DeckSelectionMode.ALL, 0.0) == {"a", "b", "c"}

    def test_all_mode_empty_corpus_returns_empty(self):
        assert rank_select(collections.Counter(), DeckSelectionMode.ALL, 0.0) == set()


# ---------------------------------------------------------------------------
# rank_select() — TOP_N mode
# ---------------------------------------------------------------------------


class TestRankSelectTopN:
    """Tests for rank_select() with DeckSelectionMode.TOP_N."""

    def test_rank_select_top_n_takes_most_frequent(self):
        assert rank_select(collections.Counter({"a": 5, "b": 3, "c": 1}), DeckSelectionMode.TOP_N, 2) == {"a", "b"}

    def test_top_n_value_exceeds_unique_returns_all(self):
        counts = collections.Counter({"a": 5, "b": 3})
        assert rank_select(counts, DeckSelectionMode.TOP_N, 100.0) == {"a", "b"}

    def test_top_n_value_zero_returns_empty(self):
        counts = collections.Counter({"a": 5, "b": 3})
        assert rank_select(counts, DeckSelectionMode.TOP_N, 0.0) == set()

    def test_top_n_negative_value_returns_empty(self):
        counts = collections.Counter({"a": 5, "b": 3})
        assert rank_select(counts, DeckSelectionMode.TOP_N, -5.0) == set()

    def test_top_n_truncates_fraction(self):
        counts = collections.Counter({"a": 5, "b": 3, "c": 1})
        assert rank_select(counts, DeckSelectionMode.TOP_N, 1.9) == {"a"}

    def test_tied_lemmas_ordered_by_first_occurrence(self):
        # "b" and "c" tie at count=5; "b" was inserted first, so TOP_N=2
        # (after top lemma "a") picks "b".
        counts: collections.Counter[str] = collections.Counter()
        counts["a"] = 10
        counts["b"] = 5
        counts["c"] = 5
        assert rank_select(counts, DeckSelectionMode.TOP_N, 2.0) == {"a", "b"}


# ---------------------------------------------------------------------------
# rank_select() — COVERAGE_PCT mode
# ---------------------------------------------------------------------------


class TestRankSelectCoveragePct:
    """Tests for rank_select() with DeckSelectionMode.COVERAGE_PCT."""

    def test_rank_select_coverage_reaches_target_with_smallest_prefix(self):
        assert rank_select(collections.Counter({"a": 6, "b": 3, "c": 1}), DeckSelectionMode.COVERAGE_PCT, 85) == {
            "a",
            "b",
        }

    def test_coverage_100_returns_all(self):
        counts = collections.Counter({"a": 50, "b": 30, "c": 15, "d": 5})
        assert rank_select(counts, DeckSelectionMode.COVERAGE_PCT, 100.0) == {"a", "b", "c", "d"}

    def test_coverage_zero_selects_nothing(self):
        counts = collections.Counter({"a": 50, "b": 30, "c": 20})
        assert rank_select(counts, DeckSelectionMode.COVERAGE_PCT, 0.0) == set()

    def test_coverage_negative_selects_nothing(self):
        counts = collections.Counter({"a": 50, "b": 30, "c": 20})
        assert rank_select(counts, DeckSelectionMode.COVERAGE_PCT, -10.0) == set()

    def test_tied_lemmas_coverage_mode_uses_first_occurrence(self):
        # a=40, b=30, c=30 total=100; target 70%: a alone is 40% < 70%,
        # a+b (insertion order) reaches 70% exactly.
        counts: collections.Counter[str] = collections.Counter()
        counts["a"] = 40
        counts["b"] = 30
        counts["c"] = 30
        assert rank_select(counts, DeckSelectionMode.COVERAGE_PCT, 70.0) == {"a", "b"}


class TestRankSelectEmptyCorpus:
    """Tests for rank_select() with an empty corpus."""

    @pytest.mark.parametrize("mode", list(DeckSelectionMode))
    def test_empty_corpus_returns_empty_set(self, mode: DeckSelectionMode):
        assert rank_select(collections.Counter(), mode, 80.0) == set()


# ---------------------------------------------------------------------------
# row_lemmas()
# ---------------------------------------------------------------------------


def _word(**overrides: object) -> TokenizedWord:
    defaults: dict[str, object] = {
        "surface": "x",
        "lemma": "a",
        "reading": "",
        "sentence": "s",
        "start_time": 0.0,
        "end_time": 1.0,
        "duration": 1.0,
    }
    defaults.update(overrides)
    return TokenizedWord(**defaults)  # type: ignore[arg-type]


class TestRowLemmas:
    """Tests for row_lemmas()."""

    def test_row_lemmas_unions_primary_and_candidates(self):
        w = TokenizedWord(surface="x", lemma="a", reading="", sentence="s", start_time=0, end_time=1, duration=1)
        w.sentence_candidates = [replace(w, lemma="b")]
        assert row_lemmas(w) == frozenset({"a", "b"})

    def test_row_lemmas_no_candidates_is_just_the_lemma(self):
        w = _word(lemma="a")
        assert row_lemmas(w) == frozenset({"a"})

    def test_row_lemmas_multiple_candidates(self):
        w = _word(lemma="a")
        w.sentence_candidates = [_word(lemma="b"), _word(lemma="c")]
        assert row_lemmas(w) == frozenset({"a", "b", "c"})


# ---------------------------------------------------------------------------
# build_preview()
# ---------------------------------------------------------------------------


class TestBuildPreview:
    """Tests for build_preview()."""

    def test_build_preview_counts_rows_hit_and_absent_lemmas(self):
        corpus = DeckCorpus(
            counts={"a": 6, "b": 3, "c": 1},
            row_lemmas=(frozenset({"a"}), frozenset({"a"}), frozenset({"c"})),
            episodes=2,
        )
        p = build_preview(corpus, {"a", "b"})
        assert (p.total_tokens, p.unique_lemmas, p.candidate_count) == (10, 3, 2)
        assert p.projected_coverage_pct == pytest.approx(90.0)
        assert p.card_count == 2 and p.known_skipped == 1

    def test_row_shared_by_two_lemmas_is_carded_once_when_either_is_selected(self):
        corpus = DeckCorpus(counts={"頭髮": 2, "头发": 1}, row_lemmas=(frozenset({"頭髮", "头发"}),), episodes=1)
        assert build_preview(corpus, {"头发"}).card_count == 1
        assert build_preview(corpus, {"頭髮", "头发"}).card_count == 1

    def test_empty_corpus_gives_zeros(self):
        corpus = DeckCorpus(counts={}, row_lemmas=(), episodes=0)
        p = build_preview(corpus, {"a"})
        assert (p.total_tokens, p.unique_lemmas, p.candidate_count) == (0, 0, 0)
        assert p.projected_coverage_pct == 0.0
        assert p.known_skipped == 0
        assert p.card_count == 0

    def test_no_row_lemmas_all_selected_are_known_skipped(self):
        corpus = DeckCorpus(counts={"a": 5, "b": 3}, row_lemmas=(), episodes=1)
        p = build_preview(corpus, {"a", "b"})
        assert p.card_count == 0
        assert p.known_skipped == 2

    def test_selected_lemma_absent_from_counts_still_counts_as_candidate(self):
        corpus = DeckCorpus(counts={"a": 5}, row_lemmas=(frozenset({"a"}),), episodes=1)
        p = build_preview(corpus, {"a", "z"})
        assert p.candidate_count == 2
        assert p.projected_coverage_pct == pytest.approx(100.0)

    def test_empty_selection_gives_zero_coverage_and_cards(self):
        corpus = DeckCorpus(counts={"a": 5, "b": 3}, row_lemmas=(frozenset({"a"}), frozenset({"b"})), episodes=1)
        p = build_preview(corpus, set())
        assert p.candidate_count == 0
        assert p.projected_coverage_pct == 0.0
        assert p.card_count == 0
        assert p.known_skipped == 0

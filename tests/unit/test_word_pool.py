"""Tests for services/word_pool.py — season-wide curation pooling helpers."""

from pathlib import Path

from anki_miner.models.stats import MiningSession
from anki_miner.models.word import TokenizedWord
from anki_miner.services.stats_service import StatsService
from anki_miner.services.word_pool import (
    CaptureCurationCallback,
    MinePassStats,
    merge_pools,
    split_selection,
    stamp_episode,
)

EP1 = Path("/media/show/ep1.mkv")
EP2 = Path("/media/show/ep2.mkv")


def _word(
    surface: str,
    *,
    sentence: str = "文",
    start: float = 1.0,
    occurrence: int = 1,
    candidates: list[TokenizedWord] | None = None,
    video: Path | None = None,
) -> TokenizedWord:
    return TokenizedWord(
        surface=surface,
        lemma=surface,
        reading="よみ",
        sentence=sentence,
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        occurrence_count=occurrence,
        sentence_candidates=candidates or [],
        video_file=video,
    )


class TestStampEpisode:
    def test_stamps_word_and_candidates(self):
        cand = _word("犬", sentence="候補")
        word = _word("犬", candidates=[cand, _word("犬", sentence="別")])
        stamp_episode([word], EP1)
        assert word.video_file == EP1
        assert all(c.video_file == EP1 for c in word.sentence_candidates)


class TestMergePools:
    def test_dedup_primary_is_first_seen(self):
        w1 = _word("猫", sentence="ep1の文", video=EP1)
        w2 = _word("猫", sentence="ep2の文", video=EP2)
        merged = merge_pools([[w1], [w2]])
        assert len(merged) == 1
        assert merged[0].sentence == "ep1の文"
        assert merged[0].video_file == EP1

    def test_occurrence_count_is_season_total(self):
        w1 = _word("猫", occurrence=3, video=EP1)
        w2 = _word("猫", occurrence=5, video=EP2)
        merged = merge_pools([[w1], [w2]])
        assert merged[0].occurrence_count == 8

    def test_unique_words_from_both_episodes_kept_in_order(self):
        merged = merge_pools([[_word("猫", video=EP1)], [_word("犬", video=EP2)]])
        assert [w.mined_form for w in merged] == ["猫", "犬"]

    def test_cross_episode_merge_builds_candidates_from_bare_words(self):
        w1 = _word("猫", sentence="ep1の文", video=EP1)
        w2 = _word("猫", sentence="ep2の文", video=EP2)
        merged = merge_pools([[w1], [w2]])
        cands = merged[0].sentence_candidates
        assert [c.sentence for c in cands] == ["ep1の文", "ep2の文"]
        assert [c.video_file for c in cands] == [EP1, EP2]

    def test_bare_word_contribution_is_leaf_copy_not_self(self):
        w1 = _word("猫", video=EP1)
        w2 = _word("猫", video=EP2)
        merged = merge_pools([[w1], [w2]])
        for cand in merged[0].sentence_candidates:
            assert cand is not merged[0]
            assert cand.sentence_candidates == []

    def test_existing_candidates_concatenated_in_episode_order(self):
        c1a = _word("猫", sentence="ep1文A", video=EP1)
        c1b = _word("猫", sentence="ep1文B", video=EP1)
        w1 = _word("猫", sentence="ep1文A", candidates=[c1a, c1b], video=EP1)
        c2a = _word("猫", sentence="ep2文A", video=EP2)
        c2b = _word("猫", sentence="ep2文B", video=EP2)
        w2 = _word("猫", sentence="ep2文A", candidates=[c2a, c2b], video=EP2)
        merged = merge_pools([[w1], [w2]])
        assert [c.sentence for c in merged[0].sentence_candidates] == [
            "ep1文A",
            "ep1文B",
            "ep2文A",
            "ep2文B",
        ]

    def test_merged_candidates_are_uncapped_by_default(self):
        pools = [[_word("猫", sentence=f"ep{i}の文", video=Path(f"/m/ep{i}.mkv"))] for i in range(20)]
        merged = merge_pools(pools)
        assert len(merged[0].sentence_candidates) == 20

    def test_candidate_cap(self):
        pools = [[_word("猫", sentence=f"ep{i}の文", video=Path(f"/m/ep{i}.mkv"))] for i in range(20)]
        merged = merge_pools(pools, max_candidates=12)
        assert len(merged[0].sentence_candidates) == 12

    def test_single_episode_single_line_word_keeps_empty_candidates(self):
        merged = merge_pools([[_word("猫", video=EP1)]])
        assert merged[0].sentence_candidates == []


class TestSplitSelection:
    def test_splits_by_video_file(self):
        w1 = _word("猫", video=EP1)
        w2 = _word("犬", video=EP2)
        subsets = split_selection([w1, w2])
        assert subsets[EP1] == [w1]
        assert subsets[EP2] == [w2]

    def test_none_video_falls_back_to_first_key(self):
        w1 = _word("猫", video=EP1)
        stray = _word("犬", video=None)
        subsets = split_selection([w1, stray])
        assert stray in subsets[EP1]

    def test_all_none_yields_single_none_group(self):
        stray = _word("犬", video=None)
        subsets = split_selection([stray])
        assert subsets == {None: [stray]}


class TestCaptureCurationCallback:
    def test_returns_empty_and_records(self):
        capture = CaptureCurationCallback()
        capture.set_episode(EP1)
        words = [_word("猫")]
        assert capture(words) == []
        capture.set_episode(EP2)
        words2 = [_word("犬")]
        assert capture(words2) == []
        assert capture.pools == [words, words2]
        assert all(w.video_file == EP1 for w in words)
        assert all(w.video_file == EP2 for w in words2)

    def test_carries_quiet_marker(self):
        assert CaptureCurationCallback.suppress_curation_messages is True


class TestMinePassStats:
    def test_record_difficulty_is_noop_but_sessions_record(self, tmp_path):
        inner = StatsService(tmp_path / "stats.db")
        assert inner.load()
        proxy = MinePassStats(inner)
        proxy.record_difficulty("Show", "ep1", 100, 40)
        assert inner.get_series_difficulty() == []
        proxy.record_session(MiningSession(series_name="Show", cards_created=2))
        assert inner.get_overall_stats().total_sessions == 1

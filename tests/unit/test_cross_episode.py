"""Tests for cross-episode frequency analysis feature."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.models import ProcessingResult, TokenizedWord
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.presenters import NullPresenter
from anki_miner.services.word_filter import WordFilterService


def _make_word(lemma="食べる", surface=None, start_time=1.0):
    return TokenizedWord(
        surface=surface or lemma,
        lemma=lemma,
        reading="",
        sentence="test",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
    )


class TestConfigCrossEpisodeFields:
    """Tests for cross-episode config fields."""

    def test_default_use_cross_episode_priority(self, test_config):
        assert test_config.use_cross_episode_priority is False

    def test_default_min_episode_appearances(self, test_config):
        assert test_config.min_episode_appearances == 2

    def test_custom_values(self, test_config):
        config = replace(
            test_config,
            use_cross_episode_priority=True,
            min_episode_appearances=3,
        )
        assert config.use_cross_episode_priority is True
        assert config.min_episode_appearances == 3


class TestFilterByEpisodeCount:
    """Tests for WordFilterService.filter_by_episode_count."""

    @pytest.fixture
    def service(self, test_config):
        return WordFilterService(test_config)

    def test_filters_words_below_threshold(self, service):
        words = [_make_word("食べる"), _make_word("走る"), _make_word("泳ぐ")]
        counts = {"食べる": 3, "走る": 1, "泳ぐ": 5}

        result = service.filter_by_episode_count(words, counts, min_appearances=2)

        assert len(result) == 2
        lemmas = {w.lemma for w in result}
        assert lemmas == {"食べる", "泳ぐ"}

    def test_no_filtering_when_min_is_one(self, service):
        words = [_make_word("食べる"), _make_word("走る")]
        counts = {"食べる": 1, "走る": 1}

        result = service.filter_by_episode_count(words, counts, min_appearances=1)

        assert len(result) == 2

    def test_word_not_in_counts_is_excluded(self, service):
        words = [_make_word("食べる"), _make_word("走る")]
        counts = {"食べる": 3}  # 走る not in counts

        result = service.filter_by_episode_count(words, counts, min_appearances=2)

        assert len(result) == 1
        assert result[0].lemma == "食べる"

    def test_empty_words_list(self, service):
        result = service.filter_by_episode_count([], {"食べる": 5}, min_appearances=2)
        assert result == []

    def test_empty_counts(self, service):
        words = [_make_word("食べる")]
        result = service.filter_by_episode_count(words, {}, min_appearances=2)
        assert result == []

    def test_exact_threshold(self, service):
        words = [_make_word("食べる")]
        counts = {"食べる": 2}

        result = service.filter_by_episode_count(words, counts, min_appearances=2)
        assert len(result) == 1


class TestCollectCrossEpisodeFrequencies:
    """Tests for FolderProcessor.collect_cross_episode_frequencies."""

    @pytest.fixture
    def mock_episode_processor(self):
        ep = MagicMock()
        ep.subtitle_parser = MagicMock()
        return ep

    @pytest.fixture
    def processor(self, mock_episode_processor):
        return FolderProcessor(
            episode_processor=mock_episode_processor,
            presenter=NullPresenter(),
        )

    def test_counts_across_episodes(self, processor, mock_episode_processor, tmp_path):
        """Word appearing in 2 episodes gets count 2."""
        pairs = [
            (tmp_path / "ep01.mkv", tmp_path / "ep01.ass"),
            (tmp_path / "ep02.mkv", tmp_path / "ep02.ass"),
        ]

        # Episode 1 has 食べる and 走る, Episode 2 has 食べる and 泳ぐ
        mock_episode_processor.subtitle_parser.parse_subtitle_file.side_effect = [
            [_make_word("食べる"), _make_word("走る")],
            [_make_word("食べる"), _make_word("泳ぐ")],
        ]

        counts = processor.collect_cross_episode_frequencies(pairs)

        assert counts["食べる"] == 2  # appears in both episodes
        assert counts["走る"] == 1  # only in ep1
        assert counts["泳ぐ"] == 1  # only in ep2

    def test_empty_pairs(self, processor):
        counts = processor.collect_cross_episode_frequencies([])
        assert counts == {}

    def test_handles_parse_error_gracefully(self, processor, mock_episode_processor, tmp_path):
        """Should skip episodes that fail to parse."""
        pairs = [
            (tmp_path / "ep01.mkv", tmp_path / "ep01.ass"),
            (tmp_path / "ep02.mkv", tmp_path / "ep02.ass"),
        ]

        mock_episode_processor.subtitle_parser.parse_subtitle_file.side_effect = [
            RuntimeError("parse error"),
            [_make_word("食べる")],
        ]

        counts = processor.collect_cross_episode_frequencies(pairs)

        assert counts["食べる"] == 1

    def test_duplicate_lemma_in_same_episode_counted_once(
        self, processor, mock_episode_processor, tmp_path
    ):
        """Same word appearing multiple times in one episode counts as 1."""
        pairs = [(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")]

        mock_episode_processor.subtitle_parser.parse_subtitle_file.return_value = [
            _make_word("食べる", start_time=1.0),
            _make_word("食べる", start_time=5.0),
        ]

        counts = processor.collect_cross_episode_frequencies(pairs)

        assert counts["食べる"] == 1


class TestTwoPassMode:
    """Tests for two-pass processing in FolderProcessor."""

    def _create_pair(self, tmp_path, name):
        (tmp_path / f"{name}.mkv").write_bytes(b"")
        (tmp_path / f"{name}.ass").write_text("", encoding="utf-8")

    def test_cross_episode_counts_passed_when_enabled(self, test_config, tmp_path):
        """When use_cross_episode_priority=True, cross_episode_counts should be passed."""
        config = replace(
            test_config,
            use_cross_episode_priority=True,
            min_episode_appearances=2,
        )

        mock_ep = MagicMock()
        mock_ep.config = config
        mock_ep.subtitle_parser = MagicMock()
        mock_ep.subtitle_parser.parse_subtitle_file.return_value = [_make_word("食べる")]
        mock_ep.process_episode.return_value = ProcessingResult(
            total_words_found=1, new_words_found=0, cards_created=0
        )

        processor = FolderProcessor(episode_processor=mock_ep, presenter=NullPresenter())
        self._create_pair(tmp_path, "ep01")

        processor.process_folder(tmp_path)

        # Verify cross_episode_counts was passed
        call_kwargs = mock_ep.process_episode.call_args
        assert call_kwargs[1]["cross_episode_counts"] is not None
        assert isinstance(call_kwargs[1]["cross_episode_counts"], dict)

    def test_cross_episode_counts_none_when_disabled(self, test_config, tmp_path):
        """When use_cross_episode_priority=False, cross_episode_counts should be None."""
        config = replace(test_config, use_cross_episode_priority=False)

        mock_ep = MagicMock()
        mock_ep.config = config
        mock_ep.process_episode.return_value = ProcessingResult(
            total_words_found=1, new_words_found=0, cards_created=0
        )

        processor = FolderProcessor(episode_processor=mock_ep, presenter=NullPresenter())
        self._create_pair(tmp_path, "ep01")

        processor.process_folder(tmp_path)

        call_kwargs = mock_ep.process_episode.call_args
        assert call_kwargs[1]["cross_episode_counts"] is None

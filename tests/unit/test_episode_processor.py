"""Tests for episode_processor module."""

import re
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import CardPayload, LineLemmas, MediaData, TokenizedWord
from anki_miner.models.youtube import FetchedMedia
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter


def _make_word(lemma="食べる", surface=None, start_time=1.0, pos="動詞"):
    return TokenizedWord(
        surface=surface or f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos=pos,
    )


def _make_media(prefix="word"):
    return MediaData(
        screenshot_path=Path(f"/tmp/{prefix}.jpg"),
        audio_path=Path(f"/tmp/{prefix}.mp3"),
        screenshot_filename=f"{prefix}.jpg",
        audio_filename=f"{prefix}.mp3",
    )


class TestProcessEpisode:
    """Tests for EpisodeProcessor.process_episode method."""

    @pytest.fixture
    def mock_services(self):
        """Create a set of mock services for the episode processor."""
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def test_full_pipeline_happy_path(self, processor, mock_services, tmp_path):
        """All 5 phases complete successfully."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        words = [_make_word("食べる"), _make_word("走る", 5.0)]
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], media1),
            (words[1], media2),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = [
            "1. to eat",
            "1. to run",
        ]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        result = processor.process_episode(video, sub)

        assert result.total_words_found == 2
        assert result.new_words_found == 2
        assert result.cards_created == 2
        assert result.success is True
        assert result.elapsed_time > 0

    def test_early_return_no_words(self, processor, mock_services, tmp_path):
        """No words found in subtitles → early return."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 0
        assert result.cards_created == 0
        mock_services["anki_service"].get_existing_vocabulary.assert_not_called()

    def test_early_return_all_words_known(self, processor, mock_services, tmp_path):
        """All words already in Anki → early return."""
        words = [_make_word()]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる"}
        mock_services["word_filter"].filter_unknown.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.new_words_found == 0
        assert result.cards_created == 0
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_preview_mode(self, processor, mock_services, tmp_path):
        """Preview mode should not extract media or create cards."""
        words = [_make_word()]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True)

        assert result.new_words_found == 1
        assert result.cards_created == 0
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_early_return_no_media(self, processor, mock_services, tmp_path):
        """No media extracted → early return with error."""
        words = [_make_word()]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 0
        assert len(result.errors) > 0
        mock_services["definition_service"].get_definitions_batch.assert_not_called()

    def test_data_flow_between_phases(self, processor, mock_services, tmp_path):
        """Verify that outputs of one phase are passed as inputs to the next."""
        video = tmp_path / "v.mkv"
        sub = tmp_path / "s.ass"
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub)

        # Verify subtitle_parser gets the subtitle file
        mock_services["subtitle_parser"].parse_subtitle_file.assert_called_once_with(sub)

        # Verify word_filter gets all_words and existing vocab
        mock_services["word_filter"].filter_unknown.assert_called_once()
        args = mock_services["word_filter"].filter_unknown.call_args
        assert args[0][0] == [word]  # all_words
        assert args[0][1] == set()  # existing_vocabulary

        # Verify media_extractor gets the video and unknown words
        mock_services["media_extractor"].extract_media_batch.assert_called_once()
        me_args = mock_services["media_extractor"].extract_media_batch.call_args
        assert me_args[0][0] == video
        assert me_args[0][1] == [word]

        # Verify definition_service gets lemmas of words with media
        mock_services["definition_service"].get_definitions_batch.assert_called_once()
        ds_args = mock_services["definition_service"].get_definitions_batch.call_args
        assert ds_args[0][0] == ["食べる"]

        # Verify anki_service gets combined CardPayload entries
        mock_services["anki_service"].create_cards_batch.assert_called_once()
        as_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = as_args[0][0]
        assert len(card_data) == 1
        assert card_data[0] == CardPayload(word=word, media=media, definition="1. to eat", extra_fields=None)

    def test_subtitle_parse_error_handling(self, processor, mock_services, tmp_path):
        """SubtitleParseError should be caught and returned as error."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = SubtitleParseError("parse failed")

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.success is False
        assert any("parse failed" in e for e in result.errors)
        assert result.elapsed_time > 0

    def test_unexpected_exception_handling(self, processor, mock_services, tmp_path):
        """Unexpected exceptions should be caught and returned as error."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = RuntimeError("unexpected")

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.success is False
        assert any("unexpected" in e.lower() for e in result.errors)

    def test_elapsed_time_positive(self, processor, mock_services, tmp_path):
        """Elapsed time should always be > 0."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.elapsed_time > 0

    def test_partial_media_extraction(self, processor, mock_services, tmp_path):
        """When only some words get media, only those should get definitions/cards."""
        words = [_make_word("食べる"), _make_word("走る", 5.0), _make_word("泳ぐ", 10.0)]
        media1 = _make_media("taberu")
        # Only first word gets media

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], media1),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Only 1 definition fetched (for the word with media)
        ds_args = mock_services["definition_service"].get_definitions_batch.call_args
        assert ds_args[0][0] == ["食べる"]

        assert result.cards_created == 1


class TestOptionalServices:
    """Tests for EpisodeProcessor with optional pitch accent and frequency services."""

    @pytest.fixture
    def mock_services(self):
        """Create a set of mock services for the episode processor."""
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_frequency_service_attaches_ranks(self, test_config, mock_services, tmp_path):
        """Frequency service should attach ranks to words after parsing."""
        word = _make_word("食べる")
        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup.return_value = 500

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify frequency lookup was called for the word
        mock_frequency.lookup.assert_called_with(word.lemma)
        # Verify the word now has a frequency rank
        assert word.frequency_rank == 500

    def test_frequency_filter_removes_words(self, test_config, mock_services, tmp_path):
        """Frequency filter should remove words outside the threshold."""
        config = replace(test_config, max_frequency_rank=1000)

        word1 = _make_word("食べる")
        word1.frequency_rank = 500
        word2 = _make_word("走る", 5.0)
        word2.frequency_rank = 5000

        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup.side_effect = [500, 5000]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # word_filter.filter_by_frequency should be called; make it filter to just word1
        mock_services["word_filter"].filter_by_frequency.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            frequency_service=mock_frequency,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify filter_by_frequency was called with the max_rank
        mock_services["word_filter"].filter_by_frequency.assert_called_once_with([word1, word2], 1000)

    def test_pitch_accent_populates_extra_fields(self, test_config, mock_services, tmp_path):
        """Pitch accent service should populate extra_fields in card data."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify card data includes pitch fields in extra_fields
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert len(card_data) == 1
        extra_fields = card_data[0].extra_fields
        assert extra_fields is not None
        assert extra_fields["pitch_position"] == "0"
        assert extra_fields["pitch_category"] == "平板"

    def test_both_services_full_pipeline(self, test_config, mock_services, tmp_path):
        """Both services active should produce card data with both extra fields."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch_detailed.return_value = [("0", "平板")]

        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup.return_value = 500

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            pitch_accent_service=mock_pitch,
            frequency_service=mock_frequency,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        extra_fields = card_data[0].extra_fields
        assert extra_fields is not None
        assert extra_fields["pitch_position"] == "0"
        assert extra_fields["pitch_category"] == "平板"
        assert extra_fields["frequency"] == "500"


class TestKnownWordDBIntegration:
    """Tests for EpisodeProcessor with known_word_db."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_known_word_db_syncs_and_refilters(self, test_config, mock_services, tmp_path):
        """Known word DB should sync with Anki and filter against the merged set in one pass.

        Performance contract: ``get_known_words`` is invoked exactly once per
        episode; the post-sync state is reconstructed in-memory by unioning
        ``anki_vocab`` with the pre-fetched set rather than re-reading SQLite.
        ``filter_unknown`` therefore runs exactly once with the merged set.
        """
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1 = _make_media("taberu")

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = {"走る"}
        mock_known_db.sync_with_anki.return_value = (1, 10)  # 1 added, 10 total

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"走る", "泳ぐ"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_known_db.sync_with_anki.assert_called_once()
        # One scan, not three.
        assert mock_known_db.get_known_words.call_count == 1
        # sync_with_anki must be called with the pre-fetched set as ``existing=``.
        sync_kwargs = mock_known_db.sync_with_anki.call_args.kwargs
        assert sync_kwargs.get("existing") == {"走る"}
        # Filter runs once against the merged set.
        assert mock_services["word_filter"].filter_unknown.call_count == 1
        merged_known = mock_services["word_filter"].filter_unknown.call_args[0][1]
        assert merged_known == {"走る", "泳ぐ"}
        assert result.cards_created == 1

    def test_known_word_db_records_mined_words(self, test_config, mock_services, tmp_path):
        """After creating cards, mined words should be added to the known word DB."""
        word = _make_word("食べる")
        media = _make_media()

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 0)

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_known_db.add_words.assert_called_once_with({"食べる"}, source="mined")


class TestWordListServiceIntegration:
    """Tests for EpisodeProcessor with word_list_service."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_word_list_service_filters_words(self, test_config, mock_services, tmp_path):
        """Word list service should apply blacklist/whitelist filtering."""
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media = _make_media()

        mock_wls = MagicMock()
        mock_wls.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # filter_by_word_lists removes word2
        mock_services["word_filter"].filter_by_word_lists.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            word_list_service=mock_wls,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_word_lists.assert_called_once_with([word1, word2], mock_wls)
        assert result.cards_created == 1


class TestCrossEpisodeFiltering:
    """Tests for cross-episode frequency filtering."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_cross_episode_counts_filters_words(self, test_config, mock_services, tmp_path):
        """Words below min_episode_appearances should be filtered out."""
        config = replace(test_config, min_episode_appearances=3)

        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media = _make_media()

        cross_counts = {"食べる": 5, "走る": 1}  # word2 appears in only 1 episode

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # filter_by_episode_count removes word2
        mock_services["word_filter"].filter_by_episode_count.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", cross_episode_counts=cross_counts)

        mock_services["word_filter"].filter_by_episode_count.assert_called_once_with([word1, word2], cross_counts, 3)
        assert result.cards_created == 1


class TestDefinitionSkipping:
    """Tests for skipping words without definitions."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_skips_words_without_definitions(self, test_config, mock_services, tmp_path):
        """Words with None definitions should be skipped when creating cards."""
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (word1, media1),
            (word2, media2),
        ]
        # word1 has a definition, word2 does not
        mock_services["definition_service"].get_definitions_batch.return_value = [
            "1. to eat",
            None,
        ]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Only 1 card should be created (word2 skipped)
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert len(card_data) == 1
        assert card_data[0].word == word1


class TestStatsServiceIntegration:
    """Tests for EpisodeProcessor with stats_service."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_records_session_on_success(self, test_config, mock_services, tmp_path):
        """Stats service should record a session after successful processing."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_stats.record_session.assert_called_once()
        mock_stats.record_difficulty.assert_called_once()

    def test_records_difficulty_after_phase2(self, test_config, mock_services, tmp_path):
        """Difficulty should be recorded with correct word counts."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        words = [_make_word("食べる"), _make_word("走る", 5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [words[0]]  # 1 unknown
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify difficulty was recorded with correct counts
        call_args = mock_stats.record_difficulty.call_args
        assert call_args.kwargs["total_words"] == 2  # len(all_words)
        assert call_args.kwargs["unknown_words"] == 1  # len(unknown_words)

    def test_no_crash_without_stats_service(self, test_config, mock_services, tmp_path):
        """Processing should work fine without stats_service."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        assert result.total_words_found == 0

    def test_no_session_recorded_on_error(self, test_config, mock_services, tmp_path):
        """Stats service should NOT record a session if processing fails."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = RuntimeError("fail")

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        assert result.success is False
        mock_stats.record_session.assert_not_called()
        mock_stats.record_difficulty.assert_not_called()


class TestPerRunTempFolder:
    """Isolate temp media per run instead of sharing one folder across calls."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_extract_media_batch_receives_unique_temp_folder_per_run(self, test_config, mock_services, tmp_path):
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], _make_media("a"))]
        mock_services["definition_service"].get_definitions_batch.return_value = ["def"]

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        calls = mock_services["media_extractor"].extract_media_batch.call_args_list
        assert len(calls) == 2
        first_folder = calls[0].kwargs["temp_folder"]
        second_folder = calls[1].kwargs["temp_folder"]
        assert first_folder is not None
        assert second_folder is not None
        assert first_folder != second_folder
        # Both folders removed on cleanup.
        assert not first_folder.exists()
        assert not second_folder.exists()

    def test_keep_temp_env_var_preserves_folder(self, test_config, mock_services, tmp_path, monkeypatch):
        monkeypatch.setenv("ANKI_MINER_KEEP_TEMP", "1")

        config = replace(test_config, media_temp_folder=tmp_path / "persisted")
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], _make_media("a"))]
        mock_services["definition_service"].get_definitions_batch.return_value = ["def"]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        folder = mock_services["media_extractor"].extract_media_batch.call_args.kwargs["temp_folder"]
        assert folder is not None
        assert folder.exists()
        # Lives under the configured base, not a random system temp dir.
        assert config.media_temp_folder in folder.parents


class TestProcessYoutubeUrl:
    """Tests for EpisodeProcessor.process_youtube_url."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _happy_pipeline(self, mock_services, word, media):
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_missing_fetcher_raises_runtime_error(self, test_config, mock_services, tmp_path):
        """process_youtube_url on a processor without a fetcher raises RuntimeError."""
        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        with pytest.raises(RuntimeError, match="YouTubeFetcherService not injected"):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

    def test_happy_path_calls_fetch_then_process_episode(self, test_config, mock_services, tmp_path):
        """process_youtube_url should call fetch_video then run the mining pipeline."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        cancel_event = threading.Event()
        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
        )

        # Fetcher was called with expected args
        mock_fetcher.fetch_video.assert_called_once()
        call = mock_fetcher.fetch_video.call_args
        assert call.args[0] == "https://youtu.be/abc123"
        assert call.args[1] == "abc123"
        assert call.args[2] == tmp_path
        assert call.args[3] == "manual_only"
        assert call.kwargs["cancel_event"] is cancel_event

        # Mining pipeline ran and produced a card
        mock_services["subtitle_parser"].parse_subtitle_file.assert_called_once_with(subtitle_file)
        assert result.cards_created == 1
        assert result.total_words_found == 1

    def test_cancel_at_entry_does_not_invoke_fetcher(self, test_config, mock_services, tmp_path):
        """Cancellation set before entry should short-circuit without calling fetch_video."""
        mock_fetcher = MagicMock()

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        cancel_event = threading.Event()
        cancel_event.set()

        result = processor.process_youtube_url(
            url="https://youtu.be/abc",
            video_id="abc",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
        )

        mock_fetcher.fetch_video.assert_not_called()
        assert result.success is False
        assert any("cancel" in e.lower() for e in result.errors)

    def test_fetcher_exception_propagates(self, test_config, mock_services, tmp_path):
        """Exceptions from the fetcher propagate; orchestrator does not swallow or cleanup."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.side_effect = RuntimeError("boom")

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        with pytest.raises(RuntimeError, match="boom"):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

        # Mining pipeline must not have run after the fetch failed.
        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_episode_identity_overridden_to_yt_video_id(self, test_config, mock_services, tmp_path):
        """Stats service should receive YT:<video_id> as episode name, not video_file.stem."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            stats_service=mock_stats,
            **mock_services,
        )

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        # Difficulty recorded with YT identity
        diff_kwargs = mock_stats.record_difficulty.call_args.kwargs
        assert diff_kwargs["episode_name"] == "YT:abc123"
        assert diff_kwargs["series_name"] == "YouTube"

        # Session recorded with YT identity
        mock_stats.record_session.assert_called_once()
        session = mock_stats.record_session.call_args.args[0]
        assert session.episode_name == "YT:abc123"
        assert session.series_name == "YouTube"

    def test_episode_name_override_preserves_default_when_none(self, test_config, mock_services, tmp_path):
        """process_episode with no override still derives identity from video_file paths."""
        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        series_dir = tmp_path / "MySeries"
        series_dir.mkdir()
        video_file = series_dir / "ep01.mkv"
        subtitle_file = series_dir / "ep01.ass"

        processor.process_episode(video_file, subtitle_file)

        diff_kwargs = mock_stats.record_difficulty.call_args.kwargs
        assert diff_kwargs["series_name"] == "MySeries"
        assert diff_kwargs["episode_name"] == "ep01"

    def _make_processor_with_fetcher(self, test_config, mock_services, tmp_path):
        """Build a processor wired to a fetcher that returns test media."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )
        return processor

    def test_curation_callback_forwarded_to_process_episode(self, test_config, mock_services, tmp_path):
        """Supplied curation_callback reaches process_episode and gets invoked."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        seen: list = []

        def _curate(words):
            seen.append(list(words))
            # Returning the same list keeps the rest of the pipeline running.
            return words

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            curation_callback=_curate,
        )

        # Callback was invoked exactly once with the post-filter word list.
        assert len(seen) == 1
        assert [w.lemma for w in seen[0]] == ["食べる"]

    def test_preview_mode_true_forwarded_to_process_episode(self, test_config, mock_services, tmp_path):
        """preview_mode=True short-circuits before card creation."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            preview_mode=True,
        )

        # Preview mode never hits anki_service.create_cards_batch.
        mock_services["anki_service"].create_cards_batch.assert_not_called()
        # And no media extraction is done either.
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        assert result.cards_created == 0

    def test_curation_and_preview_default_to_none_and_false(self, test_config, mock_services, tmp_path):
        """Omitting the new kwargs preserves pre-C3 behaviour: curation off, cards created."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        # Default behaviour: cards are created (no preview), and the pipeline
        # did not attempt to run a curation callback (we didn't pass one).
        mock_services["anki_service"].create_cards_batch.assert_called_once()
        assert result.cards_created == 1


def _make_line_lemmas(text="新しい単語", lemmas=("新しい",), start=1.0, end=3.0):
    return LineLemmas(
        line_text=text,
        lemmas=frozenset(lemmas),
        start_time=start,
        end_time=end,
        duration=end - start,
    )


class TestIPlusOneFilter:
    """Tests for the use_i_plus_one_filter wiring in EpisodeProcessor."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        word_filter.filter_i_plus_one.side_effect = lambda words, idx: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def _config_with_flag(self, test_config, *, flag: bool, dedup: bool = True):
        return replace(
            test_config,
            use_i_plus_one_filter=flag,
            deduplicate_sentences=dedup,
        )

    def _wire_happy_pipeline(self, mock_services, word, media):
        """Set up media/definitions/cards so the pipeline reaches the end."""
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_calls_parse_with_index_when_flag_on(self, test_config, mock_services, tmp_path):
        """Flag on routes Phase 1 through parse_subtitle_file_with_index."""
        config = self._config_with_flag(test_config, flag=True)
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [word],
            [line],
        )
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.assert_called_once_with(tmp_path / "s.ass")
        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_calls_legacy_parse_when_flag_off(self, test_config, mock_services, tmp_path):
        """Flag off preserves the legacy parse_subtitle_file call."""
        config = self._config_with_flag(test_config, flag=False)
        word = _make_word("食べる")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["subtitle_parser"].parse_subtitle_file.assert_called_once_with(tmp_path / "s.ass")
        mock_services["subtitle_parser"].parse_subtitle_file_with_index.assert_not_called()

    def test_skips_dedup_when_flag_on(self, test_config, mock_services, tmp_path):
        """With flag on, dedup is bypassed even if deduplicate_sentences=True."""
        config = self._config_with_flag(test_config, flag=True, dedup=True)
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [word],
            [line],
        )
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].deduplicate_by_sentence.assert_not_called()
        mock_services["word_filter"].filter_i_plus_one.assert_called_once()
        # Filter receives the unknown words and the line_index from the parser.
        call_args = mock_services["word_filter"].filter_i_plus_one.call_args
        assert call_args[0][0] == [word]
        assert call_args[0][1] == [line]

    def test_runs_dedup_when_flag_off(self, test_config, mock_services, tmp_path):
        """With flag off, dedup runs and filter_i_plus_one does not."""
        config = self._config_with_flag(test_config, flag=False, dedup=True)
        word = _make_word("食べる")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].deduplicate_by_sentence.assert_called_once()
        mock_services["word_filter"].filter_i_plus_one.assert_not_called()

    def test_presenter_message_when_filter_runs(self, test_config, mock_services, tmp_path):
        """Filter run emits 'i+1 filter: kept N/M words (P%)' via show_info."""
        config = self._config_with_flag(test_config, flag=True)
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        line1 = _make_line_lemmas(text="食べる", lemmas=("食べる",))
        line2 = _make_line_lemmas(text="走る", lemmas=("走る", "速い"), start=5.0, end=7.0)

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [word1, word2],
            [line1, line2],
        )
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # Pretend i+1 keeps only word1.
        mock_services["word_filter"].filter_i_plus_one.side_effect = lambda words, idx: [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        spy_presenter = MagicMock(spec=NullPresenter())

        processor = EpisodeProcessor(
            config=config,
            presenter=spy_presenter,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        pattern = re.compile(r"i\+1 filter: kept \d+/\d+ words \(\d+%\)")
        matched = [
            call.args[0]
            for call in spy_presenter.show_info.call_args_list
            if call.args and isinstance(call.args[0], str) and pattern.search(call.args[0])
        ]
        assert matched, (
            "Expected an i+1 filter show_info message; got: "
            f"{[c.args for c in spy_presenter.show_info.call_args_list]}"
        )
        # Specifically: kept 1/2 (50%).
        assert "kept 1/2 words (50%)" in matched[0]


class TestGlossaryFetch:
    """Tests for optional multi-dict glossary fetch in process_episode."""

    def _build_processor(self, cfg, mock_services):
        return EpisodeProcessor(
            config=cfg,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def _seed_happy_path(self, mock_services, tmp_path):
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        return tmp_path / "v.mkv", tmp_path / "s.ass"

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    def test_glossary_fetched_when_field_mapped(self, test_config, mock_services, tmp_path):
        cfg = replace(test_config, anki_fields={**test_config.anki_fields, "glossary": "Glossary"})
        processor = self._build_processor(cfg, mock_services)
        video, sub = self._seed_happy_path(mock_services, tmp_path)

        glossary_html = '<div class="yomitan-glossary"><ol><li data-dictionary="X">X def</li></ol></div>'
        mock_services["definition_service"].get_glossaries_batch.return_value = [glossary_html]

        processor.process_episode(video, sub)

        mock_services["definition_service"].get_glossaries_batch.assert_called_once()
        call_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = call_args[0][0]
        assert len(card_data) == 1
        payload = card_data[0]
        assert payload.extra_fields is not None
        assert payload.extra_fields["glossary"] == glossary_html

    def test_glossary_skipped_when_field_unmapped(self, test_config, mock_services, tmp_path):
        # Default test_config has anki_fields["glossary"] == "" (after Task 4).
        processor = self._build_processor(test_config, mock_services)
        video, sub = self._seed_happy_path(mock_services, tmp_path)

        processor.process_episode(video, sub)

        mock_services["definition_service"].get_glossaries_batch.assert_not_called()
        call_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = call_args[0][0]
        payload = card_data[0]
        # extra_fields may be None or a dict — but must NOT contain glossary.
        if payload.extra_fields is not None:
            assert "glossary" not in payload.extra_fields


class TestAudioTrackOverrideForwarding:
    """Verify process_episode forwards audio_track_override to extract_media_batch."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda words: words
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock()
        return {
            "subtitle_parser": subtitle_parser,
            "word_filter": word_filter,
            "media_extractor": media_extractor,
            "definition_service": definition_service,
            "anki_service": anki_service,
        }

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=NullPresenter(),
        )

    def test_audio_track_override_forwarded_to_extract_media_batch(self, processor, mock_services, tmp_path):
        """process_episode must pass audio_track_override to extract_media_batch."""
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        processor.process_episode(video, sub, audio_track_override=3)

        call_kwargs = mock_services["media_extractor"].extract_media_batch.call_args[1]
        assert call_kwargs.get("audio_track_override") == 3

    def test_audio_track_override_none_by_default(self, processor, mock_services, tmp_path):
        """process_episode must default audio_track_override to None."""
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        processor.process_episode(video, sub)

        call_kwargs = mock_services["media_extractor"].extract_media_batch.call_args[1]
        assert call_kwargs.get("audio_track_override") is None

    def test_process_episode_invalidates_audio_stream_cache(self, processor, mock_services, tmp_path):
        """process_episode must invalidate the per-file audio stream cache at run start.

        Prevents cross-run staleness: if the user replaces a video file on
        disk between runs, the resolver must re-probe rather than match
        against stale ffprobe output cached from the previous run.
        """
        word = _make_word()
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        processor.process_episode(video, sub)

        mock_services["media_extractor"].invalidate_audio_stream_cache.assert_called_once_with(video)

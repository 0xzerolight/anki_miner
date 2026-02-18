"""Tests for episode_processor module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter


def _make_word(lemma="食べる", surface=None, start_time=1.0):
    return TokenizedWord(
        surface=surface or f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
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

        result = processor.process_episode(
            tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True
        )

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

        # Verify anki_service gets combined (word, media, definition, extra_fields)
        mock_services["anki_service"].create_cards_batch.assert_called_once()
        as_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = as_args[0][0]
        assert len(card_data) == 1
        assert card_data[0] == (word, media, "1. to eat", None)

    def test_subtitle_parse_error_handling(self, processor, mock_services, tmp_path):
        """SubtitleParseError should be caught and returned as error."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = SubtitleParseError(
            "parse failed"
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.success is False
        assert any("parse failed" in e for e in result.errors)
        assert result.elapsed_time > 0

    def test_unexpected_exception_handling(self, processor, mock_services, tmp_path):
        """Unexpected exceptions should be caught and returned as error."""
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = RuntimeError(
            "unexpected"
        )

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
        from dataclasses import replace

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
        mock_services["word_filter"].filter_by_frequency.assert_called_once_with(
            [word1, word2], 1000
        )

    def test_pitch_accent_populates_extra_fields(self, test_config, mock_services, tmp_path):
        """Pitch accent service should populate extra_fields in card data."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch.return_value = ["0"]

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

        # Verify card data includes pitch accent in extra_fields
        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert len(card_data) == 1
        _, _, _, extra_fields = card_data[0]
        assert extra_fields is not None
        assert extra_fields["pitch_accent"] == "0"

    def test_both_services_full_pipeline(self, test_config, mock_services, tmp_path):
        """Both services active should produce card data with both extra fields."""
        word = _make_word("食べる")
        media = _make_media()

        mock_pitch = MagicMock()
        mock_pitch.is_available.return_value = True
        mock_pitch.lookup_batch.return_value = ["0"]

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
        _, _, _, extra_fields = card_data[0]
        assert extra_fields is not None
        assert extra_fields["pitch_accent"] == "0"
        assert extra_fields["frequency_rank"] == "500"


class TestStatsServiceIntegration:
    """Tests for EpisodeProcessor with stats_service."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
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
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], _make_media())
        ]
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

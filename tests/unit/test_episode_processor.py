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

        # Verify anki_service gets combined (word, media, definition)
        mock_services["anki_service"].create_cards_batch.assert_called_once()
        as_args = mock_services["anki_service"].create_cards_batch.call_args
        card_data = as_args[0][0]
        assert len(card_data) == 1
        assert card_data[0] == (word, media, "1. to eat")

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

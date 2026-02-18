"""Tests for word curation callback in EpisodeProcessor."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


class TestCurationCallback:
    """Tests for EpisodeProcessor with curation_callback parameter."""

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

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def test_curation_callback_called_with_unknown_words(self, processor, mock_services, tmp_path):
        """Curation callback should receive the filtered unknown words."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        received_words = []

        def capture_callback(word_list):
            received_words.extend(word_list)
            return word_list

        processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=capture_callback,
        )

        assert len(received_words) == 2
        assert received_words[0].lemma == "食べる"
        assert received_words[1].lemma == "走る"

    def test_curation_callback_filters_words(self, processor, mock_services, tmp_path):
        """When callback returns a subset, only those words proceed to Phase 3."""
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        # Only select the first word
        def select_first(word_list):
            return [word_list[0]]

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=select_first,
        )

        # Media extractor should only receive the selected word
        me_args = mock_services["media_extractor"].extract_media_batch.call_args
        assert me_args[0][1] == [word1]
        assert result.cards_created == 1

    def test_curation_callback_returns_empty_cancels(self, processor, mock_services, tmp_path):
        """When callback returns empty list, processing is cancelled."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=lambda w: [],  # Return empty = cancel
        )

        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 3 should not have been reached
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_curation_callback_none_normal_flow(self, processor, mock_services, tmp_path):
        """When curation_callback is None, normal flow proceeds (backward compat)."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=None,
        )

        assert result.cards_created == 1
        assert result.success is True

    def test_curation_callback_skipped_in_preview_mode(self, processor, mock_services, tmp_path):
        """In preview mode, curation callback should NOT be called."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        callback_called = False

        def should_not_be_called(word_list):
            nonlocal callback_called
            callback_called = True
            return word_list

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            preview_mode=True,
            curation_callback=should_not_be_called,
        )

        assert callback_called is False
        assert result.new_words_found == 1
        assert result.cards_created == 0

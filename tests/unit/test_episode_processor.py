"""Tests for episode_processor module."""

import re
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.exceptions import AnkiConnectionError, SetupError, SubtitleParseError
from anki_miner.models import CardPayload, LineLemmas, MediaData, TokenizedWord
from anki_miner.models.youtube import FetchedMedia
from anki_miner.orchestration.episode_processor import EpisodeProcessor, _EpisodeContext
from anki_miner.presenters import NullPresenter
from anki_miner.services.anki_service import AnkiService


def _make_episode_context(tmp_path):
    """Create a minimal _EpisodeContext for direct phase helper tests."""
    import time

    return _EpisodeContext(
        start_time=time.time(),
        video_file_str=str(tmp_path / "v.mkv"),
        subtitle_file_str=str(tmp_path / "s.ass"),
        episode_name="ep01",
        series_name="TestSeries",
        source_label="TestSeries — ep01",
    )


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

    def test_skipped_duplicates_surfaced_as_warning(self, test_config, mock_services, tmp_path):
        """A non-zero last_skipped_duplicates from card creation is reported."""
        presenter = MagicMock()
        proc = EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=presenter,
        )

        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], _make_media("taberu")),
            (words[1], _make_media("hashiru")),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. eat", "1. run"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        mock_services["anki_service"].last_skipped_duplicates = 1

        proc.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("Skipped 1" in w and "duplicate" in w.lower() for w in warnings)

    def test_media_store_failures_surfaced_as_warning(self, test_config, mock_services, tmp_path):
        """A non-zero last_media_store_failures from card creation is reported."""
        presenter = MagicMock()
        proc = EpisodeProcessor(
            config=test_config,
            subtitle_parser=mock_services["subtitle_parser"],
            word_filter=mock_services["word_filter"],
            media_extractor=mock_services["media_extractor"],
            definition_service=mock_services["definition_service"],
            anki_service=mock_services["anki_service"],
            presenter=presenter,
        )

        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [
            (words[0], _make_media("taberu")),
            (words[1], _make_media("hashiru")),
        ]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. eat", "1. run"]
        mock_services["anki_service"].create_cards_batch.return_value = 2
        mock_services["anki_service"].last_skipped_duplicates = 0
        mock_services["anki_service"].last_media_store_failures = 3

        proc.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        warnings = [c.args[0] for c in presenter.show_warning.call_args_list]
        assert any("3 media file" in w and "no audio or screenshot" in w for w in warnings)

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
        # Every card now carries an unconditional "source" stamp (Issue #69);
        # the field-level opt-in in AnkiService decides whether it lands.
        expected_source = f"{video.parent.name} — {video.stem} @ 00:00:01"
        assert card_data[0] == CardPayload(
            word=word,
            media=media,
            definition="1. to eat",
            extra_fields={"source": expected_source},
        )

    def test_audio_only_flag_reaches_extract_media_batch(self, processor, mock_services, tmp_path):
        """audio_only=True is threaded down to extract_media_batch."""
        video = tmp_path / "book.m4b"
        sub = tmp_path / "book.srt"
        word = _make_word()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub, audio_only=True)

        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["audio_only"] is True

    def test_audio_only_defaults_false(self, processor, mock_services, tmp_path):
        """Default process_episode call passes audio_only=False to the extractor."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"
        word = _make_word()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor.process_episode(video, sub)

        me_kwargs = mock_services["media_extractor"].extract_media_batch.call_args.kwargs
        assert me_kwargs["audio_only"] is False

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

    def test_bypass_optional_filters_skips_frequency(self, test_config, mock_services, tmp_path):
        """Deck Builder: bypass_optional_filters=True skips the frequency cutoff."""
        config = replace(test_config, max_frequency_rank=1000, bypass_optional_filters=True)

        word1 = _make_word("食べる")
        mock_frequency = MagicMock()
        mock_frequency.is_available.return_value = True
        mock_frequency.lookup.return_value = 500

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1]
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

        mock_services["word_filter"].filter_by_frequency.assert_not_called()

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
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (1, 10)  # 1 added, 10 total

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"走る", "泳ぐ"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
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
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 0)

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_known_db.add_words.assert_called_once_with({"食べる"}, source="mined")

    def test_locked_db_on_post_create_add_words_keeps_successful_result(self, test_config, mock_services, tmp_path):
        """A locked known_words.db during the post-create add_words must NOT
        discard a successful run's result (T-19).

        Anki (or a parallel run) can hold the SQLite file, raising
        ``OperationalError('database is locked')``. The cards were already
        created in Anki; swallowing that into the generic except path reports
        ``cards_created=0`` with no note IDs — a successful run as a failure.
        """
        import sqlite3

        word = _make_word("食べる")
        media = _make_media()

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = set()
        mock_known_db.get_words_by_source.return_value = set()
        mock_known_db.sync_with_anki.return_value = (0, 0)
        mock_known_db.add_words.side_effect = sqlite3.OperationalError("database is locked")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        mock_services["anki_service"].last_created_note_ids = [12345]

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=True),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # The lock was hit, but the successful run is preserved.
        mock_known_db.add_words.assert_called_once()
        assert result.cards_created == 1
        assert result.card_ids == [12345]
        assert not result.errors

    def test_user_ignore_list_applied_when_cache_disabled(self, test_config, mock_services, tmp_path):
        """source='user' words filter the candidate set even when use_known_words_db is off (Issue #42).

        The sync path must NOT run (cache disabled), but the user ignore list is
        still unioned into the set passed to ``filter_unknown``.
        """
        word1 = _make_word("食べる")
        word2 = _make_word("ラーメン", pos="名詞", start_time=5.0)

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_words_by_source.return_value = {"ラーメン"}

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"泳ぐ"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = []
        mock_services["anki_service"].create_cards_batch.return_value = 0

        processor = EpisodeProcessor(
            config=replace(test_config, use_known_words_db=False),
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Cache disabled → no sync, query Anki directly.
        mock_known_db.sync_with_anki.assert_not_called()
        mock_known_db.get_known_words.assert_not_called()
        mock_known_db.get_words_by_source.assert_called_once_with("user")
        # filter_unknown receives Anki vocab UNIONED with the user ignore list.
        merged_known = mock_services["word_filter"].filter_unknown.call_args[0][1]
        assert merged_known == {"泳ぐ", "ラーメン"}


class TestIncludeKnownWordsFlag:
    """Tests for the include_known_words config flag (Deck Builder bypass)."""

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

    def test_include_known_words_true_bypasses_subtraction(self, test_config, mock_services, tmp_path):
        """With include_known_words=True, filter_unknown is not called and all words pass through Phase 2."""
        config = replace(test_config, include_known_words=True)

        # Both words would normally be "known" — filter_unknown would drop them.
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        # Anki reports both words as already known.
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる", "走る"}
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1), (word2, media2)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat", "1. to run"]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # filter_unknown must NOT have been called — known-words subtraction is bypassed.
        mock_services["word_filter"].filter_unknown.assert_not_called()
        # Both words reached Phase 3 (both got media extracted).
        extract_call_args = mock_services["media_extractor"].extract_media_batch.call_args
        words_sent_to_extract = extract_call_args[0][1]
        assert len(words_sent_to_extract) == 2
        assert result.new_words_found == 2
        assert result.cards_created == 2

    def test_include_known_words_true_with_known_db_bypasses_subtraction(self, test_config, mock_services, tmp_path):
        """include_known_words=True also bypasses the known_word_db path (not just the bare Anki path)."""
        config = replace(test_config, include_known_words=True)

        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1, media2 = _make_media("taberu"), _make_media("hashiru")

        mock_known_db = MagicMock()
        mock_known_db.is_available.return_value = True
        mock_known_db.get_known_words.return_value = {"食べる", "走る"}
        mock_known_db.sync_with_anki.return_value = (0, 2)

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる", "走る"}
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1), (word2, media2)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat", "1. to run"]
        mock_services["anki_service"].create_cards_batch.return_value = 2

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            known_word_db=mock_known_db,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Neither the DB read nor filter_unknown should be called.
        mock_known_db.get_known_words.assert_not_called()
        mock_known_db.sync_with_anki.assert_not_called()
        mock_services["word_filter"].filter_unknown.assert_not_called()
        assert result.new_words_found == 2
        assert result.cards_created == 2

    def test_include_known_words_false_default_subtracts_known(self, test_config, mock_services, tmp_path):
        """Default config (include_known_words=False) preserves the standard known-words filter."""
        # test_config has include_known_words=False by default.
        assert test_config.include_known_words is False

        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media1 = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        # Anki reports word2 as known; filter_unknown returns only word1.
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"走る"}
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media1)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # filter_unknown must have been called (standard path runs).
        mock_services["word_filter"].filter_unknown.assert_called_once()
        # Only word1 (unknown) reaches Phase 3.
        words_sent_to_extract = mock_services["media_extractor"].extract_media_batch.call_args[0][1]
        assert words_sent_to_extract == [word1]
        assert result.new_words_found == 1
        assert result.cards_created == 1


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


class TestWordsetServiceIntegration:
    """Tests for EpisodeProcessor with wordset_service (Issue #59)."""

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

    def test_wordset_service_filters_words(self, test_config, mock_services, tmp_path):
        """Wordset service should drop matched proper nouns via filter_by_wordsets."""
        word1 = _make_word("食べる")
        word2 = _make_word("田中", start_time=5.0)
        media = _make_media()

        mock_ws = MagicMock()
        mock_ws.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # filter_by_wordsets removes word2 (the surname)
        mock_services["word_filter"].filter_by_wordsets.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            wordset_service=mock_ws,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # filter_by_wordsets called with both words + the wordset service + word_list_service (None)
        mock_services["word_filter"].filter_by_wordsets.assert_called_once_with([word1, word2], mock_ws, None)
        assert result.cards_created == 1

    def test_bypass_optional_filters_skips_wordset_filter(self, test_config, mock_services, tmp_path):
        """Deck Builder bypass_optional_filters=True must skip the wordset filter."""
        config = replace(test_config, bypass_optional_filters=True)

        word1 = _make_word("食べる")
        mock_ws = MagicMock()
        mock_ws.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            wordset_service=mock_ws,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_wordsets.assert_not_called()

    def test_wordset_filter_passes_word_list_service_for_whitelist(self, test_config, mock_services, tmp_path):
        """filter_by_wordsets receives the word_list_service so whitelist can rescue words."""
        word1 = _make_word("田中")
        media = _make_media()

        mock_ws = MagicMock()
        mock_ws.is_available.return_value = True

        mock_wls = MagicMock()
        mock_wls.is_available.return_value = True

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1]
        mock_services["word_filter"].filter_by_word_lists.return_value = [word1]
        mock_services["word_filter"].filter_by_wordsets.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["surname def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            wordset_service=mock_ws,
            word_list_service=mock_wls,
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify filter_by_wordsets was called with the word_list_service (for whitelist rescue)
        mock_services["word_filter"].filter_by_wordsets.assert_called_once_with([word1], mock_ws, mock_wls)


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

    def test_locked_stats_db_on_record_session_keeps_successful_result(self, test_config, mock_services, tmp_path):
        """A locked stats.db during the post-create session record must NOT
        discard a successful run's result (T-19 follow-up).

        Anki (or a parallel run) can hold the SQLite file, raising
        ``OperationalError('database is locked')``. The cards were already
        created in Anki; letting it bubble into the generic except path
        reports ``cards_created=0`` with no note IDs — a successful run as a
        failure. Same exposure as the known_words.db write one line above.
        """
        import sqlite3

        mock_stats = MagicMock()
        mock_stats.is_available.return_value = True
        mock_stats.record_session.side_effect = sqlite3.OperationalError("database is locked")

        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1
        mock_services["anki_service"].last_created_note_ids = [12345]

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            stats_service=mock_stats,
            **mock_services,
        )

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # The lock was hit, but the successful run is preserved.
        mock_stats.record_session.assert_called_once()
        assert result.cards_created == 1
        assert result.card_ids == [12345]
        assert not result.errors


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

    def test_curation_returning_none_is_cancelled(self, test_config, mock_services, tmp_path):
        """Curation callback returning None ⇒ cancelled result, no cards."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            curation_callback=lambda words: None,
        )

        mock_services["anki_service"].create_cards_batch.assert_not_called()
        assert result.cards_created == 0
        assert "Processing cancelled by user" in result.errors

    def test_curation_returning_empty_list_is_completed_zero_cards(self, test_config, mock_services, tmp_path):
        """Curation callback returning [] (confirmed, nothing selected) ⇒
        completed run with zero cards — NOT a cancellation."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            curation_callback=lambda words: [],
        )

        mock_services["anki_service"].create_cards_batch.assert_not_called()
        assert result.cards_created == 0
        assert result.new_words_found == 0
        assert "Processing cancelled by user" not in result.errors

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

    def test_on_fetched_callback_fires_with_fetched_media(self, test_config, mock_services, tmp_path):
        """on_fetched is called with the FetchedMedia returned by fetch_video."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        received: list[FetchedMedia] = []

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            on_fetched=received.append,
        )

        assert len(received) == 1
        assert isinstance(received[0], FetchedMedia)

    def test_on_fetched_callback_fires_before_process_episode(self, test_config, mock_services, tmp_path):
        """on_fetched must be invoked before the mining pipeline starts."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        self._happy_pipeline(mock_services, word, media)

        fetched_media = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = fetched_media

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

        call_order: list[str] = []

        def _on_fetched(fm):
            call_order.append("on_fetched")

        original_process_episode = processor.process_episode

        def _process_episode_spy(*args, **kwargs):
            call_order.append("process_episode")
            return original_process_episode(*args, **kwargs)

        processor.process_episode = _process_episode_spy  # type: ignore[method-assign]

        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            on_fetched=_on_fetched,
        )

        assert call_order == ["on_fetched", "process_episode"]

    def test_on_fetched_none_by_default_no_error(self, test_config, mock_services, tmp_path):
        """Omitting on_fetched (default None) runs without error."""
        processor = self._make_processor_with_fetcher(test_config, mock_services, tmp_path)

        # No exception should be raised when on_fetched is not supplied.
        result = processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        assert result.cards_created == 1


class TestProcessYoutubeUrlCancelPropagation:
    """The worker's cancel_event must reach process_episode's checkpoints (T-01).

    Historically process_youtube_url consulted cancel_event once pre-fetch and
    forwarded it only to fetch_video; the subsequent process_episode polled
    self._cancelled, which nothing set on the YouTube path — Stop All was
    ignored mid-mine and a curation dialog could pop after Stop.
    """

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

    def _build(self, test_config, mock_services, tmp_path):
        """Processor wired to a happy fetcher + happy 5-phase mocks."""
        video_file = tmp_path / "abc123.mp4"
        subtitle_file = tmp_path / "abc123.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

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
        return processor, mock_fetcher

    def _run(self, processor, tmp_path, cancel_event, **kwargs):
        return processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
            **kwargs,
        )

    def test_cancel_event_during_parse_stops_pipeline(self, test_config, mock_services, tmp_path):
        """Stop during phase 1 must end the run before media extraction."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        word = _make_word("食べる")

        def _parse_then_cancel(sub_file):
            cancel_event.set()  # user pressed Stop All mid-parse
            return [word]

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = _parse_then_cancel

        result = self._run(processor, tmp_path, cancel_event)

        assert any("cancel" in e.lower() for e in result.errors)
        assert result.cards_created == 0
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_cancel_event_during_filter_skips_curation_dialog(self, test_config, mock_services, tmp_path):
        """Stop during phase 2 must not invoke the curation callback afterwards."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        word = _make_word("食べる")

        def _filter_then_cancel(all_words, existing):
            cancel_event.set()  # Stop lands while filtering, before curation
            return [word]

        mock_services["word_filter"].filter_unknown.side_effect = _filter_then_cancel
        curation = MagicMock(name="curation_callback")

        result = self._run(processor, tmp_path, cancel_event, curation_callback=curation)

        curation.assert_not_called()
        assert any("cancel" in e.lower() for e in result.errors)
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_cancel_event_during_definitions_stops_before_card_creation(self, test_config, mock_services, tmp_path):
        """Stop during phase 4 must not create cards."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        def _define_then_cancel(lemmas, cb):
            cancel_event.set()
            return ["1. to eat"]

        mock_services["definition_service"].get_definitions_batch.side_effect = _define_then_cancel

        result = self._run(processor, tmp_path, cancel_event)

        assert any("cancel" in e.lower() for e in result.errors)
        assert result.cards_created == 0
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_cancel_event_drives_media_extractor_cancelled_check(self, test_config, mock_services, tmp_path):
        """The cancelled_check handed to extract_media_batch must reflect cancel_event live."""
        processor, _ = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()
        observed: dict[str, bool] = {}

        def _extract(video, words, cb, cancelled_check=None, temp_folder=None, **kwargs):
            observed["before"] = cancelled_check()
            cancel_event.set()  # Stop lands mid-extraction (the long ffmpeg loop)
            observed["after"] = cancelled_check()
            return []

        mock_services["media_extractor"].extract_media_batch.side_effect = _extract

        self._run(processor, tmp_path, cancel_event)

        assert observed == {"before": False, "after": True}

    def test_cancel_event_set_during_fetch_skips_mining(self, test_config, mock_services, tmp_path):
        """A cancel that lands as the fetch completes must not start the pipeline."""
        processor, mock_fetcher = self._build(test_config, mock_services, tmp_path)
        cancel_event = threading.Event()

        fetched = mock_fetcher.fetch_video.return_value

        def _fetch_then_cancel(*args, **kwargs):
            cancel_event.set()  # cancel arrives right as yt-dlp finishes
            return fetched

        mock_fetcher.fetch_video.side_effect = _fetch_then_cancel

        result = self._run(processor, tmp_path, cancel_event)

        assert any("cancel" in e.lower() for e in result.errors)
        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_cancelled_run_does_not_poison_next_run(self, test_config, mock_services, tmp_path):
        """Per-run reset: the bridge from run 1's cancel_event must not leak into run 2.

        YouTubeTab reuses ONE EpisodeProcessor across runs and _cancelled is only
        reset in __init__ — a sticky flag (or a leaked event reference) set on
        run 1 would cancel every later run.
        """
        processor, _ = self._build(test_config, mock_services, tmp_path)

        run1_event = threading.Event()

        word = _make_word("食べる")

        def _parse_then_cancel(sub_file):
            run1_event.set()
            return [word]

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = _parse_then_cancel
        result1 = self._run(processor, tmp_path, run1_event)
        assert any("cancel" in e.lower() for e in result1.errors)

        # Run 2: same processor, fresh event; run 1's event stays set.
        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = None
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        result2 = self._run(processor, tmp_path, threading.Event())

        assert processor.cancelled is False
        assert result2.cards_created == 1
        assert not result2.errors


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
        word_filter.filter_i_plus_one.side_effect = lambda words, idx, all_unknown_lemmas=None: words
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
        # Filter receives the unknown words, the line_index from the parser,
        # and the full unknown-lemma snapshot (Issue #74).
        call_args = mock_services["word_filter"].filter_i_plus_one.call_args
        assert call_args[0][0] == [word]
        assert call_args[0][1] == [line]
        assert call_args.kwargs["all_unknown_lemmas"] == {"食べる"}

    def test_i_plus_one_sees_lemmas_dropped_by_frequency_filter(self, test_config, mock_services, tmp_path):
        """Issue #74: the all_unknown_lemmas snapshot is taken BEFORE the
        frequency filter, so an unknown word outside max_frequency_rank stays
        visible to the i+1 check even though it is no longer mineable."""
        config = self._config_with_flag(test_config, flag=True)
        config = replace(config, max_frequency_rank=100)
        common = _make_word("食べる")
        rare = _make_word("拝謁", start_time=5.0)
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = (
            [common, rare],
            [line],
        )
        self._wire_happy_pipeline(mock_services, common, _make_media())
        mock_services["word_filter"].filter_unknown.return_value = [common, rare]
        # Frequency filter drops the rare word before i+1 runs.
        mock_services["word_filter"].filter_by_frequency.return_value = [common]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_args = mock_services["word_filter"].filter_i_plus_one.call_args
        assert call_args[0][0] == [common]
        assert call_args.kwargs["all_unknown_lemmas"] == {"食べる", "拝謁"}

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
        mock_services["word_filter"].filter_i_plus_one.side_effect = lambda words, idx, all_unknown_lemmas=None: [word1]
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

    def test_bypass_optional_filters_skips_i_plus_one(self, test_config, mock_services, tmp_path):
        """Deck Builder: bypass_optional_filters=True skips i+1 even when its flag is on."""
        config = replace(test_config, use_i_plus_one_filter=True, bypass_optional_filters=True)
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))

        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = ([word], [line])
        self._wire_happy_pipeline(mock_services, word, _make_media())

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_i_plus_one.assert_not_called()


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


class TestFormatTimestamp:
    """Tests for the _format_timestamp module helper (Issue #69)."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "00:00:00"),
            (59, "00:00:59"),
            (3661, "01:01:01"),
            (-5, "00:00:00"),
            (62.9, "00:01:02"),
        ],
    )
    def test_format(self, seconds, expected):
        from anki_miner.orchestration.episode_processor import _format_timestamp

        assert _format_timestamp(seconds) == expected


class TestSourceField:
    """Tests for the card "source" extra field (Issue #69)."""

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
            presenter=NullPresenter(),
            **mock_services,
        )

    def _wire_single_word(self, mock_services, word, media):
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_default_source_label_from_video_path(self, processor, mock_services, tmp_path):
        """Without an override, source_label is '<folder> — <stem>' plus timestamp."""
        word = _make_word("食べる", start_time=62.0)
        media = _make_media()
        self._wire_single_word(mock_services, word, media)

        folder = tmp_path / "My Show"
        folder.mkdir()
        video = folder / "Episode 01.mkv"
        sub = folder / "Episode 01.ass"

        processor.process_episode(video, sub)

        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert card_data[0].extra_fields["source"] == "My Show — Episode 01 @ 00:01:02"

    def test_source_label_override_wins(self, processor, mock_services, tmp_path):
        """source_label_override replaces the derived '<folder> — <stem>' origin."""
        word = _make_word("食べる", start_time=3661.0)
        media = _make_media()
        self._wire_single_word(mock_services, word, media)

        processor.process_episode(
            tmp_path / "ep01.mkv",
            tmp_path / "ep01.ass",
            source_label_override="A Cool Video Title",
        )

        card_data = mock_services["anki_service"].create_cards_batch.call_args[0][0]
        assert card_data[0].extra_fields["source"] == "A Cool Video Title @ 01:01:01"


class TestPreflightCardTarget:
    """Tests for Issue #52: pre-flight Anki target check before the mining pipeline."""

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        media_extractor = MagicMock()
        definition_service = MagicMock()
        anki_service = MagicMock(spec=AnkiService)
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

    def test_setup_error_propagates_and_aborts_pipeline(self, processor, mock_services, tmp_path):
        """SetupError from verify_card_target raises out of process_episode; Phase 1 never starts."""
        mock_services["anki_service"].verify_card_target.side_effect = SetupError("bad note type")

        with patch.object(processor, "_allocate_run_temp_folder") as mock_alloc:
            with pytest.raises(SetupError, match="bad note type"):
                processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")
            mock_alloc.assert_not_called()

        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()
        mock_services["media_extractor"].extract_media_batch.assert_not_called()
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_anki_connection_error_propagates(self, processor, mock_services, tmp_path):
        """AnkiConnectionError from verify_card_target raises out of process_episode."""
        mock_services["anki_service"].verify_card_target.side_effect = AnkiConnectionError("unreachable")

        with pytest.raises(AnkiConnectionError):
            processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["subtitle_parser"].parse_subtitle_file.assert_not_called()

    def test_preflight_called_before_subtitle_parsing(self, test_config, mock_services, tmp_path):
        """verify_card_target is called exactly once and before parse_subtitle_file."""
        word = _make_word("食べる")
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        parent = MagicMock()
        parent.attach_mock(mock_services["anki_service"], "anki_service")
        parent.attach_mock(mock_services["subtitle_parser"], "subtitle_parser")

        processor = EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_names = [c[0] for c in parent.mock_calls]
        assert "anki_service.verify_card_target" in call_names
        assert "subtitle_parser.parse_subtitle_file" in call_names
        preflight_idx = call_names.index("anki_service.verify_card_target")
        parse_idx = call_names.index("subtitle_parser.parse_subtitle_file")
        assert preflight_idx < parse_idx

        mock_services["anki_service"].verify_card_target.assert_called_once()

    def test_preview_mode_skips_preflight(self, processor, mock_services, tmp_path):
        """preview_mode=True must not call verify_card_target."""
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True)

        mock_services["anki_service"].verify_card_target.assert_not_called()

    # --- process_youtube_url pre-flight tests ---

    def _make_youtube_processor(self, test_config, mock_services, mock_fetcher):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            youtube_fetcher=mock_fetcher,
            **mock_services,
        )

    def test_youtube_setup_error_aborts_before_fetch(self, test_config, mock_services, tmp_path):
        """SetupError raised before fetch_video is called for YouTube URLs."""
        mock_services["anki_service"].verify_card_target.side_effect = SetupError("bad note type")
        mock_fetcher = MagicMock()

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        with pytest.raises(SetupError, match="bad note type"):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

        mock_fetcher.fetch_video.assert_not_called()

    def test_youtube_anki_connection_error_aborts_before_fetch(self, test_config, mock_services, tmp_path):
        """AnkiConnectionError from verify_card_target propagates before fetch_video is called."""
        mock_services["anki_service"].verify_card_target.side_effect = AnkiConnectionError("unreachable")
        mock_fetcher = MagicMock()

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        with pytest.raises(AnkiConnectionError):
            processor.process_youtube_url(
                url="https://youtu.be/abc",
                video_id="abc",
                workspace=tmp_path,
                sub_mode="manual_only",
                cancel_event=threading.Event(),
            )

        mock_fetcher.fetch_video.assert_not_called()

    def test_youtube_preflight_called_before_fetch(self, test_config, mock_services, tmp_path):
        """verify_card_target is called before fetch_video in process_youtube_url."""
        video_file = tmp_path / "abc.mp4"
        subtitle_file = tmp_path / "abc.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        media = _make_media()
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        parent = MagicMock()
        parent.attach_mock(mock_services["anki_service"], "anki_service")
        parent.attach_mock(mock_fetcher, "fetcher")

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        processor.process_youtube_url(
            url="https://youtu.be/abc",
            video_id="abc",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

        call_names = [c[0] for c in parent.mock_calls]
        assert "anki_service.verify_card_target" in call_names
        assert "fetcher.fetch_video" in call_names
        preflight_idx = call_names.index("anki_service.verify_card_target")
        fetch_idx = call_names.index("fetcher.fetch_video")
        assert preflight_idx < fetch_idx

    def test_youtube_preview_mode_skips_preflight(self, test_config, mock_services, tmp_path):
        """process_youtube_url with preview_mode=True must not call verify_card_target."""
        video_file = tmp_path / "abc.mp4"
        subtitle_file = tmp_path / "abc.ja.srt"
        video_file.touch()
        subtitle_file.touch()

        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_video.return_value = FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source="manual",
        )

        processor = self._make_youtube_processor(test_config, mock_services, mock_fetcher)

        processor.process_youtube_url(
            url="https://youtu.be/abc",
            video_id="abc",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
            preview_mode=True,
        )

        mock_services["anki_service"].verify_card_target.assert_not_called()


class TestDictionaryResourceFacade:
    """Dictionary-resource facade (T-60): GUI callers stay out of definition_service internals."""

    @pytest.fixture
    def processor(self, test_config):
        return EpisodeProcessor(
            config=test_config,
            subtitle_parser=MagicMock(),
            word_filter=MagicMock(),
            media_extractor=MagicMock(),
            definition_service=MagicMock(),
            anki_service=MagicMock(),
            presenter=NullPresenter(),
        )

    def test_release_dictionary_resources_closes_definition_service(self, processor):
        processor.release_dictionary_resources()
        processor.definition_service.close.assert_called_once_with()

    def test_release_dictionary_resources_idempotent(self, processor):
        processor.release_dictionary_resources()
        processor.release_dictionary_resources()
        assert processor.definition_service.close.call_count == 2

    def test_offline_lookup_fn_is_definition_service_offline_lookup(self, processor):
        assert processor.offline_lookup_fn is processor.definition_service.lookup_all_offline


class TestPhase2FilterOrdering:
    """Pin the order of the Phase-2 optional filters.

    The i+1 filter MUST run before the sentence-length filter: ``filter_i_plus_one``
    swaps each word's example sentence (and duration) to its chosen i+1 line, so a
    length cap applied before the swap would be silently bypassed by the swap
    target (documented invariant near episode_processor.py). The script-type
    filter runs before i+1. These use ``attach_mock`` + ``mock_calls`` so a future
    reorder trips the test.
    """

    @pytest.fixture
    def mock_services(self):
        subtitle_parser = MagicMock()
        word_filter = MagicMock()
        # All Phase-2 filters pass through so each one actually fires and the
        # pipeline reaches the next; signatures differ (positional vs kwargs).
        word_filter.deduplicate_by_sentence.side_effect = lambda w: w
        word_filter.filter_i_plus_one.side_effect = lambda words, idx, all_unknown_lemmas=None: words
        word_filter.filter_by_sentence_length.side_effect = lambda words, **kw: words
        word_filter.filter_by_script_type.side_effect = lambda words, **kw: words
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

    def _wire_pipeline_with_index(self, mock_services, word, line, media):
        mock_services["subtitle_parser"].parse_subtitle_file_with_index.return_value = ([word], [line])
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

    def test_i_plus_one_runs_before_sentence_length(self, test_config, mock_services, tmp_path):
        config = replace(
            test_config,
            use_i_plus_one_filter=True,
            use_sentence_length_filter=True,
            max_sentence_chars=40,
        )
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))
        self._wire_pipeline_with_index(mock_services, word, line, _make_media())

        parent = MagicMock()
        parent.attach_mock(mock_services["word_filter"], "word_filter")

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_names = [c[0] for c in parent.mock_calls]
        assert "word_filter.filter_i_plus_one" in call_names
        assert "word_filter.filter_by_sentence_length" in call_names
        assert call_names.index("word_filter.filter_i_plus_one") < call_names.index(
            "word_filter.filter_by_sentence_length"
        )

    def test_script_type_runs_before_i_plus_one(self, test_config, mock_services, tmp_path):
        config = replace(
            test_config,
            use_i_plus_one_filter=True,
            exclude_hiragana_only_words=True,
        )
        word = _make_word("食べる")
        line = _make_line_lemmas(lemmas=("食べる",))
        self._wire_pipeline_with_index(mock_services, word, line, _make_media())

        parent = MagicMock()
        parent.attach_mock(mock_services["word_filter"], "word_filter")

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        call_names = [c[0] for c in parent.mock_calls]
        assert "word_filter.filter_by_script_type" in call_names
        assert "word_filter.filter_i_plus_one" in call_names
        assert call_names.index("word_filter.filter_by_script_type") < call_names.index("word_filter.filter_i_plus_one")

    def test_script_type_filter_wiring(self, test_config, mock_services, tmp_path):
        """filter_by_script_type is called with the configured exclude flags and
        its output drives the rest of the pipeline."""
        config = replace(
            test_config,
            exclude_hiragana_only_words=True,
            exclude_katakana_only_words=True,
        )
        word1 = _make_word("食べる")
        word2 = _make_word("ラーメン", pos="名詞", start_time=5.0)
        media = _make_media()

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        # script-type filter drops the katakana-only word2 (override the
        # fixture's pass-through side_effect so return_value takes effect).
        mock_services["word_filter"].filter_by_script_type.side_effect = None
        mock_services["word_filter"].filter_by_script_type.return_value = [word1]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_script_type.assert_called_once_with(
            [word1, word2],
            exclude_hiragana_only=True,
            exclude_katakana_only=True,
        )
        # Only word1 (survivor) reached media extraction.
        assert mock_services["media_extractor"].extract_media_batch.call_args[0][1] == [word1]

    def test_script_type_filter_bypassed_by_optional_filters_flag(self, test_config, mock_services, tmp_path):
        """Deck Builder bypass_optional_filters=True must skip the script-type filter."""
        config = replace(
            test_config,
            exclude_hiragana_only_words=True,
            bypass_optional_filters=True,
        )
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_script_type.assert_not_called()

    def test_sentence_length_filter_bypassed_by_optional_filters_flag(self, test_config, mock_services, tmp_path):
        """bypass_optional_filters=True must skip the sentence-length filter too."""
        config = replace(
            test_config,
            use_sentence_length_filter=True,
            max_sentence_chars=40,
            bypass_optional_filters=True,
        )
        word = _make_word("食べる")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word, _make_media())]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        processor = EpisodeProcessor(config=config, presenter=NullPresenter(), **mock_services)
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        mock_services["word_filter"].filter_by_sentence_length.assert_not_called()


class TestExpressionAudio:
    """Phase-3 expression (pronunciation) audio fetching (Issue #73)."""

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

    @staticmethod
    def _enabled_config(test_config):
        """test_config with the toggle on and the expression_audio field mapped."""
        return replace(
            test_config,
            expression_audio_enabled=True,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    @staticmethod
    def _word(lemma, reading, start_time=1.0):
        word = _make_word(lemma, start_time=start_time)
        word.expression_reading = reading
        return word

    @staticmethod
    def _wire_pipeline(mock_services, pairs):
        words = [word for word, _ in pairs]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = pairs
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"] * len(words)
        mock_services["anki_service"].create_cards_batch.return_value = len(words)

    def test_enabled_fetches_per_word_and_fills_media(self, test_config, mock_services, tmp_path):
        """Fetcher called with (mined_form, expression_reading); hits fill MediaData, misses stay None."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        self._wire_pipeline(mock_services, pairs)

        audio_path = tmp_path / "jpod101_食べる_たべる.mp3"
        fetcher = MagicMock()
        fetcher.fetch.side_effect = [audio_path, None]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 2
        assert fetcher.fetch.call_count == 2
        # Verify positional args; cancelled_check is passed as a kwarg so we
        # can't use assert_any_call with positional-only matching.
        call_positional = [c.args for c in fetcher.fetch.call_args_list]
        assert ("食べる", "たべる") in call_positional
        assert ("走る", "はしる") in call_positional
        hit_media = pairs[0][1]
        assert hit_media.expression_audio_path == audio_path
        assert hit_media.expression_audio_filename == audio_path.name
        miss_media = pairs[1][1]
        assert miss_media.expression_audio_path is None
        assert miss_media.expression_audio_filename is None

    def test_miss_retries_with_lemma_for_variant_kanji_noun(self, test_config, mock_services, tmp_path):
        """Surface-form miss ⇒ retry with the unidic lemma (canonical orthography).

        Subtitle surface 噓 (variant kanji) is what JPod101 misses; the lemma
        嘘 is what it indexes. mined_form == surface for nouns, so the retry
        swaps the kanji while keeping the (unchanged) reading.
        """
        config = self._enabled_config(test_config)
        word = _make_word(lemma="嘘", surface="噓", pos="名詞")
        word.expression_reading = "うそ"
        media = _make_media("uso")
        pairs = [(word, media)]
        self._wire_pipeline(mock_services, pairs)

        # mined_form (噓) misses; lemma (嘘) hits.
        audio_path = tmp_path / "jpod101_嘘_うそ.mp3"
        fetcher = MagicMock()
        fetcher.fetch.side_effect = [None, audio_path]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert fetcher.fetch.call_count == 2
        call_positional = [c.args for c in fetcher.fetch.call_args_list]
        assert call_positional[0] == ("噓", "うそ")  # surface first
        assert call_positional[1] == ("嘘", "うそ")  # lemma fallback
        assert media.expression_audio_path == audio_path
        assert media.expression_audio_filename == audio_path.name

    def test_miss_no_lemma_retry_when_mined_form_equals_lemma(self, test_config, mock_services, tmp_path):
        """Verbs mine as lemma (mined_form == lemma) ⇒ no redundant second fetch on miss."""
        config = self._enabled_config(test_config)
        # Default pos=動詞 ⇒ mined_form == lemma == 食べる.
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch.side_effect = [None]

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert fetcher.fetch.call_count == 1
        assert pairs[0][1].expression_audio_path is None

    def test_disabled_does_not_fetch(self, test_config, mock_services, tmp_path):
        """expression_audio_enabled=False ⇒ fetcher never called, even with the field mapped."""
        config = replace(
            test_config,
            expression_audio_enabled=False,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)
        fetcher = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        fetcher.fetch.assert_not_called()

    def test_blank_field_mapping_does_not_fetch(self, test_config, mock_services, tmp_path):
        """Enabled but anki_fields['expression_audio'] blank ⇒ fetcher never called."""
        config = replace(
            test_config,
            expression_audio_enabled=True,
            anki_fields={**test_config.anki_fields, "expression_audio": ""},
        )
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)
        fetcher = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        fetcher.fetch.assert_not_called()

    def test_no_fetcher_injected_no_crash(self, test_config, mock_services, tmp_path):
        """Enabled + field mapped but fetcher=None ⇒ pipeline completes, no fetch."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert pairs[0][1].expression_audio_path is None

    def test_cancel_mid_loop_stops_fetching(self, test_config, mock_services, tmp_path):
        """Cancellation between fetches stops the loop and yields a cancelled result."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )

        def _fetch_then_cancel(mined_form, reading, cancelled_check=None):
            processor.cancel()
            return tmp_path / "a.mp3"

        fetcher.fetch.side_effect = _fetch_then_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert fetcher.fetch.call_count == 1
        assert "Processing cancelled by user" in result.errors
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_presenter_receives_summary_line(self, test_config, mock_services, tmp_path):
        """Presenter gets the 'Expression audio: X/Y available' info line."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
        ]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch.side_effect = [tmp_path / "a.mp3", None]
        presenter = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=presenter,
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        presenter.show_info.assert_any_call("Expression audio: 1/2 available")

    def test_fetcher_receives_cancelled_check_kwarg(self, test_config, mock_services, tmp_path):
        """fetch() is called with cancelled_check= that reflects processor.cancelled."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert fetcher.fetch.call_count == 1
        call_kwargs = fetcher.fetch.call_args.kwargs
        assert "cancelled_check" in call_kwargs
        # The callable should return False (processor not cancelled) and be callable.
        check_fn = call_kwargs["cancelled_check"]
        assert callable(check_fn)
        assert check_fn() is False

    def test_progress_emitted_per_word(self, test_config, mock_services, tmp_path):
        """progress_callback.on_progress is called once per word during the expression audio loop."""
        config = self._enabled_config(test_config)
        pairs = [
            (self._word("食べる", "たべる"), _make_media("taberu")),
            (self._word("走る", "はしる", 5.0), _make_media("hashiru")),
            (self._word("飲む", "のむ", 9.0), _make_media("nomu")),
        ]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch.return_value = None

        progress_callback = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=progress_callback)

        # The raw callback is wrapped by StageWeightedProgress, which forwards
        # on_progress for every word in the expression-audio loop with the
        # item_description "Expression audio: <mined_form>".  Filter to only
        # those calls and assert exactly 3 (one per word) — other on_progress
        # calls (e.g. the finish() snap to 100 with "") belong to different
        # stages.
        expr_audio_calls = [
            c for c in progress_callback.on_progress.call_args_list if c.args[1].startswith("Expression audio:")
        ]
        assert len(expr_audio_calls) == 3


class TestExpressionAudioProgressBand:
    """Progress-accounting tests for the expression-audio stage (Issue #73 fix).

    Verifies that _phase3_extract correctly consumes the dedicated progress band
    registered by process_episode — no band theft from definitions or later stages.
    """

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

    @staticmethod
    def _enabled_config(test_config):
        return replace(
            test_config,
            expression_audio_enabled=True,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )

    @staticmethod
    def _wire_pipeline(mock_services, pairs):
        words = [word for word, _ in pairs]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = pairs
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. def"] * len(words)
        mock_services["anki_service"].create_cards_batch.return_value = len(words)

    @staticmethod
    def _word(lemma, reading="よみ", start_time=1.0):
        word = _make_word(lemma, start_time=start_time)
        word.expression_reading = reading
        return word

    def test_feature_on_stage_count_matches_bands(self, test_config, mock_services, tmp_path):
        """Feature ON: on_start call count equals number of registered bands.

        With expression_audio active the bands are: extract, expression_audio,
        definitions, cards = 4.  StageWeightedProgress forwards on_start only
        once to the inner callback (the global on_start), so we check on_start
        descriptions to count stage entries instead.
        """
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch.return_value = None

        # Use a recording callback that counts on_start calls by description
        class _RecordingCallback:
            def __init__(self):
                self.starts = []
                self.completes = 0

            def on_start(self, total, description):
                self.starts.append(description)

            def on_progress(self, current, item_description):
                pass

            def on_complete(self):
                self.completes += 1

            def on_error(self, item_description, error_message):
                pass

        cb = _RecordingCallback()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=cb)

        # StageWeightedProgress only forwards on_start to the inner callback
        # once — on the very first stage (extract). All subsequent on_start
        # calls from later stages (expression audio, definitions, cards) only
        # advance the internal band counter and never reach the inner callback.
        # Therefore cb.starts has exactly 1 entry regardless of band count.
        # The expression-audio band being registered is verified indirectly:
        # fetcher.fetch was called (feature ran) AND finish() emitted one
        # on_complete, confirming the full 4-band sweep completed without
        # band-accounting errors.
        assert len(cb.starts) == 1
        assert cb.completes == 1  # from StageWeightedProgress.finish()

        # Cross-check: fetcher was called (expression-audio band ran)
        assert fetcher.fetch.call_count == 1

    def test_feature_on_on_start_description_includes_expression_audio(self, test_config, mock_services, tmp_path):
        """The expression-audio on_start description is passed to the inner callback.

        Because StageWeightedProgress only forwards on_start once (first stage),
        we pass the raw callback directly to _phase3_extract to inspect all
        on_start calls without the wrapper.
        """
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        fetcher.fetch.return_value = None

        # Pass a raw MagicMock as progress_callback so we can inspect all calls.
        raw_cb = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        # Check that on_start was called with "Fetching expression audio" description
        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert any("expression audio" in d.lower() for d in on_start_descriptions)

    def test_feature_on_zero_media_results_band_still_consumed(self, test_config, mock_services, tmp_path):
        """Feature ON + empty media_results: band consumed (on_start(0) + on_complete called).

        The gate in _phase3_extract must NOT include `media_results` non-empty —
        otherwise the band is silently skipped and the next stage steals it.
        We call _phase3_extract directly with a raw callback (bypassing
        StageWeightedProgress) so every on_start/on_complete lands on our mock.
        """
        config = self._enabled_config(test_config)

        fetcher = MagicMock()
        fetcher.fetch.return_value = None

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )

        # extract_media_batch returns empty — simulates total extraction failure
        mock_services["media_extractor"].extract_media_batch.return_value = []

        raw_cb = MagicMock()

        ctx = _make_episode_context(tmp_path)
        # Call _phase3_extract directly with the raw callback (no wrapper)
        result = processor._phase3_extract(
            ctx=ctx,
            video_file=tmp_path / "v.mkv",
            unknown_words=[self._word("食べる", "たべる")],
            progress_callback=raw_cb,
            run_temp_folder=tmp_path,
        )

        # Band must be consumed: on_start(0, "Fetching expression audio") + on_complete
        assert raw_cb.on_start.call_count == 1
        on_start_args = raw_cb.on_start.call_args
        assert on_start_args.args[0] == 0  # total = 0 (empty media_results)
        assert "expression audio" in on_start_args.args[1].lower()
        assert raw_cb.on_complete.call_count == 1
        # Fetcher never called — no words to iterate
        fetcher.fetch.assert_not_called()
        # Returns empty list unchanged
        assert result == []

    def test_feature_off_no_expression_audio_on_start(self, test_config, mock_services, tmp_path):
        """Feature OFF: no expression-audio on_start; baseline stage count unchanged."""
        # Feature disabled (expression_audio_enabled=False)
        config = replace(
            test_config,
            expression_audio_enabled=False,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        fetcher = MagicMock()
        raw_cb = MagicMock()

        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            expression_audio_fetcher=fetcher,
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert not any("expression audio" in d.lower() for d in on_start_descriptions)
        fetcher.fetch.assert_not_called()

    def test_feature_off_no_fetcher_no_expression_audio_on_start(self, test_config, mock_services, tmp_path):
        """Feature enabled but no fetcher injected: no expression-audio band."""
        config = self._enabled_config(test_config)
        pairs = [(self._word("食べる", "たべる"), _make_media("taberu"))]
        self._wire_pipeline(mock_services, pairs)

        raw_cb = MagicMock()

        # No fetcher injected
        processor = EpisodeProcessor(
            config=config,
            presenter=NullPresenter(),
            **mock_services,
        )
        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", progress_callback=raw_cb)

        on_start_descriptions = [c.args[1] for c in raw_cb.on_start.call_args_list]
        assert not any("expression audio" in d.lower() for d in on_start_descriptions)

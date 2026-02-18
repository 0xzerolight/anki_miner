"""Tests for comprehension percentage feature."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models import MediaData, ProcessingResult, TokenizedWord
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


class TestComprehensionPercentageModel:
    """Tests for comprehension_percentage field on ProcessingResult."""

    def test_default_is_zero(self):
        result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0)
        assert result.comprehension_percentage == 0.0

    def test_custom_value(self):
        result = ProcessingResult(
            total_words_found=10,
            new_words_found=2,
            cards_created=2,
            comprehension_percentage=80.0,
        )
        assert result.comprehension_percentage == 80.0

    def test_backward_compatible(self):
        """Existing code creating ProcessingResult without comprehension should still work."""
        result = ProcessingResult(
            total_words_found=5,
            new_words_found=3,
            cards_created=3,
            errors=[],
            elapsed_time=1.5,
        )
        assert result.comprehension_percentage == 0.0


class TestComprehensionCalculation:
    """Tests for comprehension percentage calculation in EpisodeProcessor."""

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

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def test_100_percent_when_all_known(self, processor, mock_services, tmp_path):
        """All words known → 100% comprehension."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる", "走る"}
        mock_services["word_filter"].filter_unknown.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.comprehension_percentage == 100.0

    def test_0_percent_when_none_known(self, processor, mock_services, tmp_path):
        """No words known → 0% comprehension."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        # Preview mode to avoid needing media/definitions
        mock_services["media_extractor"].extract_media_batch.return_value = []

        result = processor.process_episode(
            tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True
        )

        assert result.comprehension_percentage == 0.0

    def test_partial_comprehension(self, processor, mock_services, tmp_path):
        """Some words known → correct percentage."""
        words = [
            _make_word("食べる"),
            _make_word("走る", start_time=5.0),
            _make_word("泳ぐ", start_time=10.0),
            _make_word("読む", start_time=15.0),
        ]
        # 3 out of 4 known → 75%
        unknown = [words[3]]

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = {
            "食べる",
            "走る",
            "泳ぐ",
        }
        mock_services["word_filter"].filter_unknown.return_value = unknown

        result = processor.process_episode(
            tmp_path / "v.mkv", tmp_path / "s.ass", preview_mode=True
        )

        assert result.comprehension_percentage == 75.0

    def test_empty_words_gives_zero(self, processor, mock_services, tmp_path):
        """No words found in subtitles → 0% comprehension."""
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = []

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.comprehension_percentage == 0.0

    def test_comprehension_in_full_pipeline(self, processor, mock_services, tmp_path):
        """Comprehension should be set even in full card-creation pipeline."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        unknown = [words[1]]  # 1 of 2 known → 50%
        media = _make_media("hashiru")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"食べる"}
        mock_services["word_filter"].filter_unknown.return_value = unknown
        mock_services["media_extractor"].extract_media_batch.return_value = [(unknown[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to run"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.comprehension_percentage == 50.0
        assert result.cards_created == 1

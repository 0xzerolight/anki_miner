"""Tests for cancellation support in EpisodeProcessor and MediaExtractorService."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.models import MediaData, TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter
from anki_miner.services.media_extractor import MediaExtractorService


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


class TestEpisodeProcessorCancel:
    """Tests for EpisodeProcessor cancellation between phases."""

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

    def test_cancel_flag_initially_false(self, processor):
        """Cancel flag should be False after construction."""
        assert processor.cancelled is False

    def test_cancel_sets_flag(self, processor):
        """cancel() should set the cancelled flag to True."""
        processor.cancel()
        assert processor.cancelled is True

    def test_cancel_after_phase1(self, processor, mock_services, tmp_path):
        """Cancel after subtitle parsing returns partial result."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words

        # Cancel after phase 1 — side_effect on subtitle_parser to trigger cancel
        def parse_and_cancel(sub_file):
            result = words
            processor.cancel()  # Cancel after phase 1
            return result

        mock_services["subtitle_parser"].parse_subtitle_file.side_effect = parse_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 2 should NOT have been reached
        mock_services["anki_service"].get_existing_vocabulary.assert_not_called()

    def test_cancel_after_phase2(self, processor, mock_services, tmp_path):
        """Cancel after filtering returns partial result with word counts."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        # Cancel during phase 2 — side_effect on filter_unknown
        def filter_and_cancel(all_words, existing):
            processor.cancel()
            return words

        mock_services["word_filter"].filter_unknown.side_effect = filter_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 2
        assert result.new_words_found == 2
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 3 should NOT have been reached
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_cancel_after_phase3(self, processor, mock_services, tmp_path):
        """Cancel after media extraction returns partial result."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        def extract_and_cancel(video, ws, cb, cancelled_check=None):
            processor.cancel()
            return [(words[0], media)]

        mock_services["media_extractor"].extract_media_batch.side_effect = extract_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.new_words_found == 1
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 4 should NOT have been reached
        mock_services["definition_service"].get_definitions_batch.assert_not_called()

    def test_cancel_after_phase4(self, processor, mock_services, tmp_path):
        """Cancel after definitions returns partial result."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]

        def define_and_cancel(lemmas, cb):
            processor.cancel()
            return ["1. to eat"]

        mock_services["definition_service"].get_definitions_batch.side_effect = define_and_cancel

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.total_words_found == 1
        assert result.new_words_found == 1
        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 5 should NOT have been reached
        mock_services["anki_service"].create_cards_batch.assert_not_called()

    def test_cancel_not_set_runs_full_pipeline(self, processor, mock_services, tmp_path):
        """When not cancelled, full pipeline runs normally."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = 1

        result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert result.cards_created == 1
        assert result.success is True

    def test_cancelled_check_passed_to_media_extractor(self, processor, mock_services, tmp_path):
        """Verify that cancelled_check callable is passed to extract_media_batch."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        # Verify cancelled_check was passed as a keyword argument
        call_kwargs = mock_services["media_extractor"].extract_media_batch.call_args[1]
        assert "cancelled_check" in call_kwargs
        assert callable(call_kwargs["cancelled_check"])

    def test_cancelled_check_reflects_processor_state(self, processor, mock_services, tmp_path):
        """The cancelled_check callable should reflect processor._cancelled."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        cancelled_check = mock_services["media_extractor"].extract_media_batch.call_args[1][
            "cancelled_check"
        ]

        assert cancelled_check() is False
        processor.cancel()
        assert cancelled_check() is True


class TestMediaExtractorBatchCancel:
    """Tests for MediaExtractorService.extract_media_batch() cancellation."""

    MODULE = "anki_miner.services.media_extractor"

    @pytest.fixture
    def service(self, test_config):
        with patch(f"{self.MODULE}.ensure_directory"):
            return MediaExtractorService(test_config)

    @pytest.fixture
    def video_file(self, tmp_path):
        return tmp_path / "episode_01.mkv"

    def test_cancelled_mid_batch(self, service, video_file, make_tokenized_word, tmp_path):
        """Should return partial results when cancelled_check returns True mid-batch."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
            make_tokenized_word(lemma="走る", start_time=5.0),
        ]

        call_count = 0

        def fake_extract(vf, word):
            nonlocal call_count
            call_count += 1
            ss = tmp_path / f"{word.lemma}_cancel.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        # Cancel after first item
        items_seen = 0

        def cancelled_check():
            nonlocal items_seen
            items_seen += 1
            return items_seen > 1  # Cancel after first item processed

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, cancelled_check=cancelled_check)

        # Should have at least 1 result but not all 3
        assert len(result) >= 1
        assert len(result) < 3

    def test_no_cancelled_check_processes_all(
        self, service, video_file, make_tokenized_word, tmp_path
    ):
        """Should process all words when cancelled_check is None."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
        ]

        def fake_extract(vf, word):
            ss = tmp_path / f"{word.lemma}_all.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words)

        assert len(result) == 2

    def test_cancelled_before_any_processing(
        self, service, video_file, make_tokenized_word, tmp_path
    ):
        """Should return empty list when cancelled immediately."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        def fake_extract(vf, word):
            ss = tmp_path / f"{word.lemma}_imm.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, cancelled_check=lambda: True)

        # Cancelled immediately, so no results should be collected
        assert len(result) == 0

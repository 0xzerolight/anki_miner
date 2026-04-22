"""Tests for folder_processor module."""

from unittest.mock import MagicMock

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models import ProcessingResult
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.presenters import NullPresenter


class TestProcessFolder:
    """Tests for FolderProcessor.process_folder method."""

    @pytest.fixture
    def mock_episode_processor(self):
        mock = MagicMock()
        mock.config = AnkiMinerConfig(use_cross_episode_priority=False)
        return mock

    @pytest.fixture
    def processor(self, mock_episode_processor):
        return FolderProcessor(
            episode_processor=mock_episode_processor,
            presenter=NullPresenter(),
        )

    def _create_pair(self, tmp_path, name):
        """Create a video/subtitle pair in tmp_path."""
        (tmp_path / f"{name}.mkv").write_bytes(b"")
        (tmp_path / f"{name}.ass").write_text("", encoding="utf-8")

    def test_processes_all_pairs(self, processor, mock_episode_processor, tmp_path):
        """Should process every video/subtitle pair found."""
        self._create_pair(tmp_path, "ep01")
        self._create_pair(tmp_path, "ep02")

        mock_episode_processor.process_episode.return_value = ProcessingResult(
            total_words_found=5, new_words_found=3, cards_created=3
        )

        results = processor.process_folder(tmp_path)

        assert len(results) == 2
        assert mock_episode_processor.process_episode.call_count == 2

    def test_empty_folder(self, processor, tmp_path):
        """Empty folder should return empty list."""
        results = processor.process_folder(tmp_path)
        assert results == []

    def test_accumulates_cards(self, processor, mock_episode_processor, tmp_path):
        """Total cards should be summed across all episodes."""
        self._create_pair(tmp_path, "ep01")
        self._create_pair(tmp_path, "ep02")

        mock_episode_processor.process_episode.side_effect = [
            ProcessingResult(total_words_found=10, new_words_found=5, cards_created=5),
            ProcessingResult(total_words_found=8, new_words_found=3, cards_created=3),
        ]

        results = processor.process_folder(tmp_path)

        total = sum(r.cards_created for r in results)
        assert total == 8

    def test_handles_per_episode_exception(self, processor, mock_episode_processor, tmp_path):
        """Exception in one episode should not stop others."""
        self._create_pair(tmp_path, "ep01")
        self._create_pair(tmp_path, "ep02")

        mock_episode_processor.process_episode.side_effect = [
            RuntimeError("ep01 failed"),
            ProcessingResult(total_words_found=5, new_words_found=3, cards_created=3),
        ]

        results = processor.process_folder(tmp_path)

        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is True

    def test_reports_progress(
        self, processor, mock_episode_processor, tmp_path, recording_progress
    ):
        """Should report progress via callback."""
        self._create_pair(tmp_path, "ep01")

        mock_episode_processor.process_episode.return_value = ProcessingResult(
            total_words_found=5, new_words_found=3, cards_created=3
        )

        processor.process_folder(tmp_path, progress_callback=recording_progress)

        assert len(recording_progress.starts) == 1
        assert recording_progress.starts[0][0] == 1  # 1 pair
        assert len(recording_progress.progresses) == 1
        assert recording_progress.completes == 1

    def test_passes_preview_mode(self, processor, mock_episode_processor, tmp_path):
        """Preview mode should be forwarded to episode processor."""
        self._create_pair(tmp_path, "ep01")

        mock_episode_processor.process_episode.return_value = ProcessingResult(
            total_words_found=5, new_words_found=3, cards_created=0
        )

        processor.process_folder(tmp_path, preview_mode=True)

        call_kwargs = mock_episode_processor.process_episode.call_args
        assert call_kwargs[1]["preview_mode"] is True

    def test_no_nested_progress(self, processor, mock_episode_processor, tmp_path):
        """Episode processor should receive progress_callback=None."""
        self._create_pair(tmp_path, "ep01")

        mock_episode_processor.process_episode.return_value = ProcessingResult(
            total_words_found=5, new_words_found=3, cards_created=3
        )

        processor.process_folder(tmp_path, progress_callback=MagicMock())

        call_kwargs = mock_episode_processor.process_episode.call_args
        assert call_kwargs[1]["progress_callback"] is None

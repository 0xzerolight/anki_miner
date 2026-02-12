"""Tests for folder_processor module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.models import ProcessingResult
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.presenters import NullPresenter


class TestFindVideoSubtitlePairs:
    """Tests for FolderProcessor.find_video_subtitle_pairs method."""

    @pytest.fixture
    def processor(self):
        mock_ep = MagicMock()
        return FolderProcessor(
            episode_processor=mock_ep,
            presenter=NullPresenter(),
        )

    def test_matching_pairs(self, processor, tmp_path):
        """Should pair video and subtitle files with same base name."""
        (tmp_path / "ep01.mkv").write_bytes(b"")
        (tmp_path / "ep01.ass").write_text("", encoding="utf-8")
        (tmp_path / "ep02.mp4").write_bytes(b"")
        (tmp_path / "ep02.srt").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        assert len(pairs) == 2
        assert all(isinstance(v, Path) and isinstance(s, Path) for v, s in pairs)

    def test_multiple_video_extensions(self, processor, tmp_path):
        """Should recognize .mp4, .mkv, .avi, .m4v, .mov."""
        for ext in [".mp4", ".mkv", ".avi", ".m4v", ".mov"]:
            name = f"video{ext}"
            (tmp_path / name).write_bytes(b"")
            (tmp_path / f"video{ext}").with_suffix(".ass").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        assert len(pairs) == 5

    def test_picks_one_subtitle_when_multiple_exist(self, processor, tmp_path):
        """When both .ass and .srt exist, should pick exactly one (first found in set iteration)."""
        (tmp_path / "ep01.mkv").write_bytes(b"")
        (tmp_path / "ep01.ass").write_text("", encoding="utf-8")
        (tmp_path / "ep01.srt").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        assert len(pairs) == 1
        assert pairs[0][1].suffix in {".ass", ".srt"}

    def test_naturally_sorted(self, processor, tmp_path):
        """Should sort by natural order (ep2 before ep10)."""
        for name in ["ep10", "ep2", "ep1"]:
            (tmp_path / f"{name}.mkv").write_bytes(b"")
            (tmp_path / f"{name}.ass").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        video_names = [v.stem for v, _ in pairs]
        assert video_names == ["ep1", "ep2", "ep10"]

    def test_empty_for_mismatched(self, processor, tmp_path):
        """Videos without matching subtitles should not be paired."""
        (tmp_path / "video.mkv").write_bytes(b"")
        (tmp_path / "other.ass").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        assert len(pairs) == 0

    def test_ignores_non_video_files(self, processor, tmp_path):
        """Non-video files should be ignored."""
        (tmp_path / "readme.txt").write_text("", encoding="utf-8")
        (tmp_path / "readme.ass").write_text("", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"")
        (tmp_path / "image.ass").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        assert len(pairs) == 0

    def test_ignores_subdirectories(self, processor, tmp_path):
        """Should not descend into subdirectories."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "ep01.mkv").write_bytes(b"")
        (sub / "ep01.ass").write_text("", encoding="utf-8")

        pairs = processor.find_video_subtitle_pairs(tmp_path)

        assert len(pairs) == 0


class TestProcessFolder:
    """Tests for FolderProcessor.process_folder method."""

    @pytest.fixture
    def mock_episode_processor(self):
        return MagicMock()

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

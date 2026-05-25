"""Tests for subtitle_viewer module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer
from anki_miner.utils.audio_track_detector import JapaneseAudioStream

MODULE = "anki_miner.gui.widgets.subtitle_viewer"

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


class TestFormatTime:
    """Tests for SubtitleViewer._format_time static method."""

    def test_zero_ms(self):
        """Should format 0ms as 00:00."""
        assert SubtitleViewer._format_time(0) == "00:00"

    def test_one_second(self):
        """Should format 1000ms as 00:01."""
        assert SubtitleViewer._format_time(1000) == "00:01"

    def test_one_minute(self):
        """Should format 60000ms as 01:00."""
        assert SubtitleViewer._format_time(60000) == "01:00"

    def test_mixed_time(self):
        """Should format 90500ms as 01:30."""
        assert SubtitleViewer._format_time(90500) == "01:30"

    def test_large_time(self):
        """Should format large times correctly."""
        # 25 minutes 13 seconds = 1513000 ms
        assert SubtitleViewer._format_time(1513000) == "25:13"

    def test_negative_ms(self):
        """Should treat negative values as 00:00."""
        assert SubtitleViewer._format_time(-1000) == "00:00"

    def test_sub_second(self):
        """Should truncate sub-second values."""
        assert SubtitleViewer._format_time(999) == "00:00"

    def test_over_one_hour(self):
        """Should handle times over 60 minutes."""
        # 75 minutes = 4500000 ms
        assert SubtitleViewer._format_time(4500000) == "75:00"


@pytest.fixture
def fake_media_classes():
    """Patch QMediaPlayer + QAudioOutput so construction skips backend media loading."""
    with (
        patch(f"{MODULE}.QMediaPlayer") as player_cls,
        patch(f"{MODULE}.QAudioOutput") as audio_cls,
    ):
        player_instance = MagicMock()
        player_instance.audioTracks.return_value = []
        player_cls.return_value = player_instance
        audio_cls.return_value = MagicMock()
        yield {"player": player_instance, "player_cls": player_cls}


class TestAudioTrackSelection:
    """Test that the Japanese audio track is selected in the mini-player."""

    def test_records_audio_index_when_japanese_found(self, fake_media_classes):
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=2, audio_index=1, language_tag="jpn"),
        ):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        assert viewer._jp_audio_index == 1

    def test_records_none_when_no_japanese_track(self, fake_media_classes):
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        assert viewer._jp_audio_index is None

    def test_setup_media_connects_tracks_changed(self, fake_media_classes):
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        fake_media_classes["player"].tracksChanged.connect.assert_called()

    def test_on_tracks_changed_selects_japanese_track(self, fake_media_classes):
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=2, audio_index=1, language_tag="jpn"),
        ):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        viewer._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(1)

    def test_on_tracks_changed_noop_when_no_japanese(self, fake_media_classes):
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        viewer._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_on_tracks_changed_bounds_check_skips_out_of_range(self, fake_media_classes):
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=5, audio_index=3, language_tag="jpn"),
        ):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        # Player reports fewer tracks than ffprobe found.
        player.audioTracks.return_value = [MagicMock()]

        viewer._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

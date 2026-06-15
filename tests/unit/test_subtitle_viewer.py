"""Tests for subtitle_viewer module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

# Patch targets now live in the player widget module.
PLAYER_MODULE = "anki_miner.gui.widgets.subtitle_player_widget"


@pytest.fixture
def fake_media_classes():
    """Patch QMediaPlayer + QAudioOutput in the player widget module."""
    with (
        patch(f"{PLAYER_MODULE}.QMediaPlayer") as player_cls,
        patch(f"{PLAYER_MODULE}.QAudioOutput") as audio_cls,
    ):
        player_instance = MagicMock()
        player_instance.audioTracks.return_value = []
        player_cls.return_value = player_instance
        audio_cls.return_value = MagicMock()
        yield {"player": player_instance, "player_cls": player_cls}


class TestSubtitleViewerEmbeds:
    """Test that SubtitleViewer correctly embeds SubtitlePlayerWidget."""

    def test_viewer_has_player_widget_attribute(self, qtbot, fake_media_classes):
        """SubtitleViewer should expose a player_widget attribute."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        assert isinstance(viewer.player_widget, SubtitlePlayerWidget)

    def test_viewer_calls_set_source_on_player_widget(self, qtbot, fake_media_classes):
        """SubtitleViewer.__init__ should call player_widget.set_source."""
        entries = [(1.0, 2.0, "test")]
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), entries, 0.5)
        qtbot.addWidget(viewer)
        # set_source stores the entries and offset on the player widget
        assert viewer.player_widget.subtitle_entries == entries
        assert viewer.player_widget._offset == 0.5

    def test_viewer_forwards_audio_track_override(self, qtbot, fake_media_classes):
        """SubtitleViewer should forward audio_track_override to set_source."""
        with patch(
            f"{PLAYER_MODULE}.find_japanese_audio_stream",
            side_effect=AssertionError("ffprobe should not be called"),
        ):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        qtbot.addWidget(viewer)
        assert viewer.player_widget._jp_audio_index == 2

    def test_viewer_delegates_all_args_to_set_source(self, qtbot):
        """SubtitleViewer.__init__ should pass video_path, entries, offset, audio_track_override, ffprobe_cmd."""
        video_path = Path("/tmp/test.mkv")
        entries = [(1.0, 2.0, "hello")]
        with patch.object(SubtitlePlayerWidget, "set_source") as mock_set_source:
            viewer = SubtitleViewer(video_path, entries, 1.5, audio_track_override=3)
        qtbot.addWidget(viewer)
        mock_set_source.assert_called_once_with(
            video_path,
            entries,
            1.5,
            audio_track_override=3,
            ffprobe_cmd="ffprobe",
        )

    def test_viewer_forwards_ffprobe_cmd_to_set_source(self, qtbot):
        """SubtitleViewer should forward an explicit ffprobe_cmd to player_widget.set_source."""
        video_path = Path("/tmp/test.mkv")
        entries = [(1.0, 2.0, "hello")]
        with patch.object(SubtitlePlayerWidget, "set_source") as mock_set_source:
            viewer = SubtitleViewer(video_path, entries, 0.0, ffprobe_cmd="/custom/ffprobe")
        qtbot.addWidget(viewer)
        mock_set_source.assert_called_once_with(
            video_path,
            entries,
            0.0,
            audio_track_override=None,
            ffprobe_cmd="/custom/ffprobe",
        )


class TestSubtitleViewerOffset:
    """Tests for SubtitleViewer offset management."""

    def test_get_offset_returns_initial_value(self, qtbot, fake_media_classes):
        """get_offset() should return the initial offset passed to the constructor."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 1.5)
        qtbot.addWidget(viewer)
        assert viewer.get_offset() == 1.5

    def test_offset_change_updates_get_offset(self, qtbot, fake_media_classes):
        """Changing the spinbox should update get_offset()."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.offset_spinbox.setValue(2.5)
        assert viewer.get_offset() == 2.5

    def test_offset_change_forwards_to_player_widget(self, qtbot, fake_media_classes):
        """Changing the spinbox should forward the new offset to player_widget."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.offset_spinbox.setValue(1.0)
        assert viewer.player_widget._offset == 1.0


class TestSubtitleViewerStops:
    """Tests that SubtitleViewer stops the player on close/accept/reject."""

    def test_accept_stops_player(self, qtbot, fake_media_classes):
        """accept() should stop the player."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.accept()
        fake_media_classes["player"].stop.assert_called()

    def test_reject_stops_player(self, qtbot, fake_media_classes):
        """reject() should stop the player."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.reject()
        fake_media_classes["player"].stop.assert_called()

    def test_close_event_stops_player(self, qtbot, fake_media_classes):
        """closeEvent() should stop the player."""
        with patch(f"{PLAYER_MODULE}.find_japanese_audio_stream", return_value=None):
            viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.close()
        fake_media_classes["player"].stop.assert_called()

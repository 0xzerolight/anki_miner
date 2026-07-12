"""Tests for subtitle_viewer module (embedded libmpv backend)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

# Patch targets live in the player widget module (its mpv import seam).
PLAYER_MODULE = "anki_miner.gui.widgets.subtitle_player_widget"


@pytest.fixture
def fake_mpv(monkeypatch):
    """Patch the player widget's mpv seam; yields the fake player instance.

    ``has_render_context`` is forced True so loadfile runs at set_source
    (offscreen unshown widgets never create a real render context; the
    deferred-load behavior is covered in test_subtitle_player_widget.py)."""
    from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget

    player = MagicMock(name="mpv.MPV")
    player.pause = True
    player.track_list = []
    player.event_callback.return_value = lambda fn: fn
    monkeypatch.setattr(MpvVideoWidget, "has_render_context", property(lambda self: True))
    with (
        patch(f"{PLAYER_MODULE}.mpv_available", return_value=True),
        patch(f"{PLAYER_MODULE}.create_mpv_player", return_value=player) as factory,
    ):
        yield {"player": player, "factory": factory}


class TestSubtitleViewerEmbeds:
    """Test that SubtitleViewer correctly embeds SubtitlePlayerWidget."""

    def test_viewer_has_player_widget_attribute(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        assert isinstance(viewer.player_widget, SubtitlePlayerWidget)
        assert viewer.player_widget.player is fake_mpv["player"]

    def test_viewer_calls_set_source_on_player_widget(self, qtbot, fake_mpv):
        entries = [(1.0, 2.0, "test")]
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), entries, 0.5)
        qtbot.addWidget(viewer)
        assert viewer.player_widget.subtitle_entries == entries
        assert viewer.player_widget._offset == 0.5
        fake_mpv["player"].loadfile.assert_called_once_with("/tmp/fake.mkv")

    def test_viewer_forwards_audio_track_override(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        qtbot.addWidget(viewer)
        assert viewer.player_widget._audio_track_override == 2

    def test_viewer_delegates_all_args_to_set_source(self, qtbot):
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
        )


class TestSubtitleViewerOffset:
    """Tests for SubtitleViewer offset management."""

    def test_get_offset_returns_initial_value(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 1.5)
        qtbot.addWidget(viewer)
        assert viewer.get_offset() == 1.5

    def test_offset_change_updates_get_offset(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.offset_spinbox.setValue(2.5)
        assert viewer.get_offset() == 2.5

    def test_offset_change_forwards_to_player_widget(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.offset_spinbox.setValue(1.0)
        assert viewer.player_widget._offset == 1.0


class TestSubtitleViewerReleases:
    """SubtitleViewer must terminate the mpv core on close/accept/reject."""

    def test_accept_releases_player(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.accept()
        fake_mpv["player"].terminate.assert_called_once()
        assert viewer.player_widget.player is None

    def test_reject_releases_player(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.reject()
        fake_mpv["player"].terminate.assert_called_once()

    def test_close_event_releases_player(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0)
        qtbot.addWidget(viewer)
        viewer.close()
        fake_mpv["player"].terminate.assert_called_once()

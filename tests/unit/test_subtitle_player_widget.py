"""Tests for SubtitlePlayerWidget."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QLocale
from PyQt6.QtMultimedia import QMediaPlayer

from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
from anki_miner.utils.audio_track_detector import JapaneseAudioStream

MODULE = "anki_miner.gui.widgets.subtitle_player_widget"


@pytest.fixture
def fake_media_classes():
    """Patch QMediaPlayer + QAudioOutput so construction skips backend media loading.

    QVideoWidget is NOT patched here — it instantiates fine under QT_QPA_PLATFORM=offscreen
    and must be a real QWidget for Qt's layout machinery to accept it.
    """
    with (
        patch(f"{MODULE}.QMediaPlayer") as player_cls,
        patch(f"{MODULE}.QAudioOutput") as audio_cls,
    ):
        player_instance = MagicMock()
        player_instance.audioTracks.return_value = []
        player_cls.return_value = player_instance
        audio_cls.return_value = MagicMock()
        yield {"player": player_instance, "player_cls": player_cls, "audio_cls": audio_cls}


class TestSubtitlePlayerWidgetInit:
    """Tests for SubtitlePlayerWidget.__init__."""

    def test_init_does_not_create_player(self, qtbot):
        """No QMediaPlayer should be created until set_source is called."""
        with (
            patch(f"{MODULE}.QMediaPlayer") as player_cls,
            patch(f"{MODULE}.QAudioOutput") as audio_cls,
        ):
            _widget = SubtitlePlayerWidget()
            qtbot.addWidget(_widget)
            player_cls.assert_not_called()
            audio_cls.assert_not_called()

    def test_init_sets_default_attributes(self, qtbot):
        """Default attributes should be falsy/None before set_source."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        assert widget._jp_audio_index is None
        assert widget._audio_track_override is None
        assert widget._offset == 0.0
        assert widget.subtitle_entries == []

    def test_init_with_parent(self, qtbot):
        """Should accept a parent argument without error."""
        widget = SubtitlePlayerWidget(parent=None)
        qtbot.addWidget(widget)
        assert widget is not None


class TestSetSource:
    """Tests for SubtitlePlayerWidget.set_source."""

    def test_set_source_creates_player(self, qtbot, fake_media_classes):
        """set_source should create QMediaPlayer and QAudioOutput."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        fake_media_classes["player_cls"].assert_called_once()
        fake_media_classes["audio_cls"].assert_called_once()

    def test_set_source_stores_entries_and_offset(self, qtbot, fake_media_classes):
        """set_source should store subtitle_entries and offset."""
        entries = [(1.0, 2.0, "Hello"), (3.0, 4.0, "World")]
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), entries, 0.5)
        assert widget.subtitle_entries == entries
        assert widget._offset == 0.5

    def test_set_source_records_audio_index_when_japanese_found(self, qtbot, fake_media_classes):
        """set_source should store ffprobe's audio_index."""
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=2, audio_index=1, language_tag="jpn"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        assert widget._jp_audio_index == 1

    def test_set_source_records_none_when_no_japanese_track(self, qtbot, fake_media_classes):
        """set_source should store None when ffprobe finds no Japanese track."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        assert widget._jp_audio_index is None

    def test_set_source_forwards_ffprobe_cmd(self, qtbot, fake_media_classes):
        """set_source should forward ffprobe_cmd to find_japanese_audio_stream."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None) as mock_find:
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, ffprobe_cmd="/custom/ffprobe")
        mock_find.assert_called_once_with(Path("/tmp/fake.mkv"), ffprobe_cmd="/custom/ffprobe")

    def test_set_source_defaults_ffprobe_cmd_literal(self, qtbot, fake_media_classes):
        """set_source should default ffprobe_cmd to the bare 'ffprobe' literal."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None) as mock_find:
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        mock_find.assert_called_once_with(Path("/tmp/fake.mkv"), ffprobe_cmd="ffprobe")

    def test_set_source_override_skips_ffprobe(self, qtbot, fake_media_classes):
        """With an audio_track_override, ffprobe (and ffprobe_cmd) is never invoked."""
        with patch(f"{MODULE}.find_japanese_audio_stream") as mock_find:
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2, ffprobe_cmd="/custom/ffprobe")
        mock_find.assert_not_called()
        assert widget._jp_audio_index == 2

    def test_set_source_connects_tracks_changed(self, qtbot, fake_media_classes):
        """set_source should connect the tracksChanged signal."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        fake_media_classes["player"].tracksChanged.connect.assert_called()

    def test_set_source_twice_stops_previous_player(self, qtbot, fake_media_classes):
        """Calling set_source a second time should stop the previous player."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            first_player = fake_media_classes["player"]
            # Reset mock to isolate second call
            first_player.reset_mock()
            widget.set_source(Path("/tmp/other.mkv"), [], 0.0)
        first_player.stop.assert_called_once()

    def test_set_source_twice_fully_tears_down_first_player(self, qtbot):
        """Calling set_source a second time should disconnect signals, clear audio, and deleteLater on the first player."""
        mock1 = MagicMock()
        mock1.audioTracks.return_value = []
        mock2 = MagicMock()
        mock2.audioTracks.return_value = []

        with (
            patch(f"{MODULE}.QMediaPlayer", side_effect=[mock1, mock2]),
            patch(f"{MODULE}.QAudioOutput"),
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.set_source(Path("/tmp/other.mkv"), [], 0.0)

        # The first player must be fully torn down
        mock1.stop.assert_called()
        mock1.positionChanged.disconnect.assert_called_once_with(widget._on_position_changed)
        mock1.durationChanged.disconnect.assert_called_once_with(widget._on_duration_changed)
        mock1.playbackStateChanged.disconnect.assert_called_once_with(widget._on_playback_state_changed)
        mock1.errorOccurred.disconnect.assert_called_once_with(widget._on_media_error)
        mock1.tracksChanged.disconnect.assert_called_once_with(widget._on_tracks_changed)
        mock1.mediaStatusChanged.disconnect.assert_called_once_with(widget._on_media_status_changed)
        mock1.setAudioOutput.assert_any_call(None)
        mock1.deleteLater.assert_called_once()

    def test_set_source_with_audio_track_override(self, qtbot, fake_media_classes):
        """audio_track_override should skip ffprobe and use the given index."""
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            side_effect=AssertionError("ffprobe should not be called"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        assert widget._jp_audio_index == 2

    def test_set_source_default_offset_zero(self, qtbot, fake_media_classes):
        """Default offset should be 0.0 when not specified."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [])
        assert widget._offset == 0.0


class TestSeekSeconds:
    """Tests for SubtitlePlayerWidget.seek_seconds."""

    def test_seek_seconds_calls_set_position(self, qtbot, fake_media_classes):
        """seek_seconds should call player.setPosition with ms value."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.seek_seconds(5.0)
        fake_media_classes["player"].setPosition.assert_called_with(5000)

    def test_seek_seconds_clamps_negative_to_zero(self, qtbot, fake_media_classes):
        """Negative seek values should be clamped to 0."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.seek_seconds(-3.0)
        fake_media_classes["player"].setPosition.assert_called_with(0)

    def test_seek_seconds_fractional(self, qtbot, fake_media_classes):
        """Fractional seconds should be converted to int ms."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.seek_seconds(1.5)
        fake_media_classes["player"].setPosition.assert_called_with(1500)


class TestSetOffset:
    """Tests for SubtitlePlayerWidget.set_offset."""

    def test_set_offset_updates_internal_offset(self, qtbot):
        """set_offset should update _offset attribute."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.set_offset(1.5)
        assert widget._offset == 1.5

    def test_set_offset_negative(self, qtbot):
        """set_offset should accept negative values."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.set_offset(-2.0)
        assert widget._offset == -2.0


class TestPlayPauseStop:
    """Tests for play/pause/stop API."""

    def test_play_delegates_to_player(self, qtbot, fake_media_classes):
        """play() should call player.play()."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.play()
        fake_media_classes["player"].play.assert_called()

    def test_pause_delegates_to_player(self, qtbot, fake_media_classes):
        """pause() should call player.pause()."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.pause()
        fake_media_classes["player"].pause.assert_called()

    def test_stop_delegates_to_player(self, qtbot, fake_media_classes):
        """stop() should call player.stop()."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            widget.stop()
        fake_media_classes["player"].stop.assert_called()

    def test_stop_without_player_does_not_raise(self, qtbot):
        """stop() before set_source should not raise."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.stop()  # should not raise


class TestTogglePlayPause:
    """Tests for the public toggle_play_pause() control (Issue #55)."""

    def test_toggle_pauses_when_playing(self, qtbot, fake_media_classes):
        """When playing, toggle should pause."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player_cls = fake_media_classes["player_cls"]
        player.playbackState.return_value = player_cls.PlaybackState.PlayingState

        widget.toggle_play_pause()

        player.pause.assert_called_once()
        player.play.assert_not_called()

    def test_toggle_plays_when_not_playing(self, qtbot, fake_media_classes):
        """When paused/stopped, toggle should play."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player_cls = fake_media_classes["player_cls"]
        player.playbackState.return_value = player_cls.PlaybackState.PausedState

        widget.toggle_play_pause()

        player.play.assert_called_once()
        player.pause.assert_not_called()

    def test_toggle_without_player_does_not_raise(self, qtbot):
        """toggle_play_pause() before set_source must be a no-op."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.toggle_play_pause()  # should not raise


class TestPlaybackStateLabel:
    """Tests for the play-button label state machine (Issue #55 review gap).

    The button text must be driven by the actual playbackState signal, not
    toggled manually, so an end-of-media stop resets it to "Play" on its own.
    Uses the real QMediaPlayer enum (no media-class patch) so the handler's
    ``state == PlayingState`` comparison resolves against real enum members.
    """

    def test_label_shows_pause_when_playing(self, qtbot):
        from PyQt6.QtMultimedia import QMediaPlayer

        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
        assert widget.play_button.text() == "Pause"

    def test_label_shows_play_when_paused(self, qtbot):
        from PyQt6.QtMultimedia import QMediaPlayer

        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.play_button.setText("Pause")
        widget._on_playback_state_changed(QMediaPlayer.PlaybackState.PausedState)
        assert widget.play_button.text() == "Play"

    def test_label_resets_to_play_on_end_of_media_stop(self, qtbot):
        """End-of-media transitions to StoppedState — label must reset to Play."""
        from PyQt6.QtMultimedia import QMediaPlayer

        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.play_button.setText("Pause")
        widget._on_playback_state_changed(QMediaPlayer.PlaybackState.StoppedState)
        assert widget.play_button.text() == "Play"


class TestAudioTrackSelection:
    """Test that the Japanese audio track is selected in the player widget."""

    def test_on_tracks_changed_selects_japanese_track(self, qtbot, fake_media_classes):
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=2, audio_index=1, language_tag="jpn"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(1)

    def test_on_tracks_changed_noop_when_no_japanese(self, qtbot, fake_media_classes):
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_on_tracks_changed_bounds_check_skips_out_of_range(self, qtbot, fake_media_classes):
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=5, audio_index=3, language_tag="jpn"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_override_skips_ffprobe(self, qtbot, fake_media_classes):
        """When audio_track_override is set, ffprobe should not be called."""
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            side_effect=AssertionError("ffprobe should not be called"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        assert widget._jp_audio_index == 2

    def test_override_used_in_on_tracks_changed(self, qtbot, fake_media_classes):
        """Override index should be passed to setActiveAudioTrack."""
        with patch(f"{MODULE}.find_japanese_audio_stream", side_effect=AssertionError("should not call ffprobe")):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock(), MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(2)

    def test_qt_metadata_fallback_finds_japanese(self, qtbot, fake_media_classes):
        """When ffprobe returns None and no override, Qt metadata should find Japanese track."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]

        track_en = MagicMock()
        track_en.value.return_value = QLocale.Language.English
        track_jp = MagicMock()
        track_jp.value.return_value = QLocale.Language.Japanese
        player.audioTracks.return_value = [track_en, track_jp, track_en]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(1)

    def test_qt_metadata_fallback_skipped_when_ffprobe_found_jp(self, qtbot, fake_media_classes):
        """When ffprobe found Japanese, Qt fallback should not run."""
        with patch(
            f"{MODULE}.find_japanese_audio_stream",
            return_value=JapaneseAudioStream(global_index=0, audio_index=0, language_tag="jpn"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]

        track_en = MagicMock()
        track_en.value.return_value = QLocale.Language.English
        player.audioTracks.return_value = [track_en, track_en]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(0)
        for track in player.audioTracks.return_value:
            track.value.assert_not_called()

    def test_qt_metadata_fallback_no_japanese_leaves_default(self, qtbot, fake_media_classes):
        """When ffprobe and Qt metadata both fail, setActiveAudioTrack should not be called."""
        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]

        track_en = MagicMock()
        track_en.value.return_value = QLocale.Language.English
        player.audioTracks.return_value = [track_en, track_en]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_override_index_zero_selects_first_track(self, qtbot, fake_media_classes):
        """audio_track_override=0 is a valid first-track index."""
        with patch(f"{MODULE}.find_japanese_audio_stream") as mock_find_jp:
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=0)
        mock_find_jp.assert_not_called()
        assert widget._jp_audio_index == 0

        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]
        widget._on_tracks_changed()
        player.setActiveAudioTrack.assert_called_once_with(0)

    def test_override_logs_in_first_branch_not_qt_branch(self, qtbot, fake_media_classes, caplog):
        """Override path should log 'Selected audio track', not 'Qt metadata'."""
        with patch(f"{MODULE}.find_japanese_audio_stream", side_effect=AssertionError("should not call ffprobe")):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=1)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        with caplog.at_level(logging.INFO, logger="anki_miner.gui.widgets.subtitle_player_widget"):
            widget._on_tracks_changed()

        assert any("Selected audio track" in r.message for r in caplog.records)
        assert not any("Qt metadata" in r.message for r in caplog.records)


class TestFormatTime:
    """Tests for SubtitlePlayerWidget._format_time static method."""

    def test_zero_ms(self):
        assert SubtitlePlayerWidget._format_time(0) == "00:00"

    def test_one_second(self):
        assert SubtitlePlayerWidget._format_time(1000) == "00:01"

    def test_one_minute(self):
        assert SubtitlePlayerWidget._format_time(60000) == "01:00"

    def test_mixed_time(self):
        assert SubtitlePlayerWidget._format_time(90500) == "01:30"

    def test_large_time(self):
        assert SubtitlePlayerWidget._format_time(1513000) == "25:13"

    def test_negative_ms(self):
        assert SubtitlePlayerWidget._format_time(-1000) == "00:00"

    def test_sub_second(self):
        assert SubtitlePlayerWidget._format_time(999) == "00:00"

    def test_over_one_hour(self):
        assert SubtitlePlayerWidget._format_time(4500000) == "75:00"


class TestSetSourceCreatesPlayer:
    """set_source always builds a QMediaPlayer and hands it the source.

    There is no codec gate at set_source: a QMediaPlayer is always created for
    every source, including AV1. AV1 plays in-app when the machine has a hardware
    AV1 decoder (RTX-30+/Tiger-Lake+). When no decoded video frame arrives within
    the watchdog window (2 s after LoadedMedia), the widget hides the video area
    and shows a fallback notice + "Open in external player" button instead.
    """

    def test_av1_creates_player(self, qtbot, fake_media_classes):
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/av1.mkv"), [], 0.0)
        fake_media_classes["player_cls"].assert_called_once()
        assert widget.player is not None

    def test_supported_codec_creates_player(self, qtbot, fake_media_classes):
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="h264"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
        fake_media_classes["player_cls"].assert_called_once()
        assert widget.player is not None


class TestAv1WatchdogFallback:
    """Tests for the AV1 first-decoded-frame watchdog and fallback UI.

    The watchdog arms on LoadedMedia/BufferedMedia when the source is AV1 and no
    video frame has arrived yet.  If no frame arrives within 2 s, the timeout
    handler hides the video widget and shows the fallback notice + open-externally
    button.  A frame arriving before the timeout cancels the watchdog and keeps
    normal playback UI visible.  Non-AV1 sources never arm the watchdog.
    """

    def _make_widget_av1(self, qtbot, fake_media_classes):
        """Helper: build a widget with an AV1 source loaded (watchdog NOT yet armed)."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/av1.mkv"), [], 0.0)
        return widget

    # ------------------------------------------------------------------
    # 1. AV1 + watchdog fires without a frame → fallback shown
    # ------------------------------------------------------------------

    def test_watchdog_fires_shows_fallback_hides_video(self, qtbot, fake_media_classes):
        """When the AV1 watchdog fires with no frame, fallback UI is visible and video is hidden."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)

        # Arm the watchdog by simulating LoadedMedia (no frame arrived yet).
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert widget._av1_watchdog.isActive(), "Watchdog should be armed after LoadedMedia"

        # Fire the watchdog directly (avoids real 2-second wait).
        widget._on_av1_watchdog_timeout()

        # Use isHidden() — the widget isn't show()n, so isVisible() requires parent to be shown.
        assert widget.video_widget.isHidden(), "video_widget should be hidden after fallback"
        assert not widget._av1_notice_label.isHidden(), "fallback notice should be visible"
        assert not widget._av1_open_button.isHidden(), "open-external button should be visible"

    def test_watchdog_fires_stops_player(self, qtbot, fake_media_classes):
        """When the watchdog fires, the player is stopped."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        fake_media_classes["player"].reset_mock()
        widget._on_av1_watchdog_timeout()

        fake_media_classes["player"].stop.assert_called_once()

    def test_watchdog_arms_on_buffered_media_too(self, qtbot, fake_media_classes):
        """Watchdog should arm on BufferedMedia as well as LoadedMedia."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
        assert widget._av1_watchdog.isActive()

    # ------------------------------------------------------------------
    # 2. AV1 + frame arrives before timeout → no fallback
    # ------------------------------------------------------------------

    def test_frame_before_timeout_suppresses_fallback(self, qtbot, fake_media_classes):
        """If a video frame arrives before the watchdog fires, fallback is never shown."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)

        # Simulate a decoded frame arriving (sets _got_video_frame and stops timer).
        widget._on_video_frame_changed()

        # Now simulate LoadedMedia — watchdog should NOT arm because frame already arrived.
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert not widget._av1_watchdog.isActive(), "Watchdog should not arm after a frame was decoded"

        # Trigger timeout handler anyway — should be a no-op.
        widget._on_av1_watchdog_timeout()

        # Use isHidden() — the widget isn't show()n, so isVisible() requires parent to be shown.
        assert not widget.video_widget.isHidden(), "video_widget should stay visible"
        assert widget._av1_notice_label.isHidden(), "fallback notice should remain hidden"
        assert widget._av1_open_button.isHidden(), "open-external button should remain hidden"

    def test_frame_cancels_armed_watchdog(self, qtbot, fake_media_classes):
        """A frame arriving after LoadedMedia but before timeout cancels the watchdog."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert widget._av1_watchdog.isActive()

        # Frame arrives — should stop the timer.
        widget._on_video_frame_changed()
        assert not widget._av1_watchdog.isActive(), "Frame should have cancelled the watchdog"

        # Timeout fires late (e.g. race) — no fallback since _got_video_frame is True.
        widget._on_av1_watchdog_timeout()
        assert not widget.video_widget.isHidden(), "video_widget should still be visible"

    # ------------------------------------------------------------------
    # 3. Non-AV1 source → watchdog never arms
    # ------------------------------------------------------------------

    def test_non_av1_watchdog_never_arms(self, qtbot, fake_media_classes):
        """For a non-AV1 source, LoadedMedia must not arm the watchdog."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="h264"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/h264.mkv"), [], 0.0)

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert not widget._av1_watchdog.isActive(), "Non-AV1 source must not arm the watchdog"

    def test_non_av1_no_fallback_on_timeout_call(self, qtbot, fake_media_classes):
        """Even if the timeout handler is called directly for a non-AV1 source, no fallback is shown."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="hevc"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/hevc.mkv"), [], 0.0)

        # Watchdog is never armed for non-AV1 sources.
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert not widget._av1_watchdog.isActive()

        # Even a direct call to the timeout handler must be a no-op when _is_av1 is False.
        widget._on_av1_watchdog_timeout()
        assert not widget.video_widget.isHidden(), "video_widget must stay visible for non-AV1"
        assert widget._av1_notice_label.isHidden(), "fallback notice must remain hidden for non-AV1"
        assert widget._av1_open_button.isHidden(), "open-external button must remain hidden for non-AV1"

    # ------------------------------------------------------------------
    # 4. set_source resets fallback state for a new source
    # ------------------------------------------------------------------

    def test_set_source_resets_fallback_on_new_source(self, qtbot, fake_media_classes):
        """Loading a second source must hide the fallback UI and show the video widget."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)

        # Trigger fallback state.
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        widget._on_av1_watchdog_timeout()
        assert widget.video_widget.isHidden(), "video_widget should be hidden in fallback state"
        assert not widget._av1_notice_label.isHidden(), "fallback notice should be visible"

        # Load a second source (non-AV1) — state must reset.
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="h264"),
        ):
            widget.set_source(Path("/tmp/h264.mkv"), [], 0.0)

        # Use isHidden() — the widget isn't show()n, so isVisible() requires parent to be shown.
        assert not widget.video_widget.isHidden(), "video_widget should be restored on new source"
        assert widget._av1_notice_label.isHidden(), "fallback notice should be hidden on new source"
        assert widget._av1_open_button.isHidden(), "open-external button should be hidden on new source"

"""Tests for SubtitlePlayerWidget."""

import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from PyQt6.QtCore import QEvent, QLocale
from PyQt6.QtMultimedia import QMediaPlayer

from anki_miner.gui.widgets.subtitle_player_widget import (
    _AV1_NUDGE_FALLBACK_MS,
    _AV1_WATCHDOG_MS,
    SubtitlePlayerWidget,
)
from anki_miner.utils.audio_track_detector import JapaneseAudioStream

MODULE = "anki_miner.gui.widgets.subtitle_player_widget"


def _set_source_sync(qtbot, widget, *args, timeout=2000, **kwargs):
    """Call set_source and block until its async ffprobe configures the player.

    The probe now runs off the GUI thread; the QMediaPlayer is built in a
    GUI-thread callback once it returns. Callers that patch the probe functions
    must keep the patch active until the worker thread has run, so this waiter is
    invoked *inside* the patch context.
    """
    widget.set_source(*args, **kwargs)
    qtbot.waitUntil(lambda: widget.player is not None, timeout=timeout)


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
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            fake_media_classes["player_cls"].assert_called_once()
            fake_media_classes["audio_cls"].assert_called_once()

    def test_set_source_stores_entries_and_offset(self, qtbot, fake_media_classes):
        """set_source should store subtitle_entries and offset."""
        entries = [(1.0, 2.0, "Hello"), (3.0, 4.0, "World")]
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), entries, 0.5)
            # Entries/offset are stored synchronously, before the probe completes.
            assert widget.subtitle_entries == entries
            assert widget._offset == 0.5

    def test_set_source_records_audio_index_when_japanese_found(self, qtbot, fake_media_classes):
        """set_source should store ffprobe's audio_index."""
        with (
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                return_value=JapaneseAudioStream(global_index=2, audio_index=1, language_tag="jpn"),
            ),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            assert widget._jp_audio_index == 1

    def test_set_source_records_none_when_no_japanese_track(self, qtbot, fake_media_classes):
        """set_source should store None when ffprobe finds no Japanese track."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            assert widget._jp_audio_index is None

    def test_set_source_forwards_ffprobe_cmd(self, qtbot, fake_media_classes):
        """set_source should forward ffprobe_cmd to find_japanese_audio_stream."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None) as mock_find,
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, ffprobe_cmd="/custom/ffprobe")
            mock_find.assert_called_once_with(Path("/tmp/fake.mkv"), ffprobe_cmd="/custom/ffprobe")

    def test_set_source_defaults_ffprobe_cmd_literal(self, qtbot, fake_media_classes):
        """set_source should default ffprobe_cmd to the bare 'ffprobe' literal."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None) as mock_find,
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            mock_find.assert_called_once_with(Path("/tmp/fake.mkv"), ffprobe_cmd="ffprobe")

    def test_set_source_override_skips_ffprobe(self, qtbot, fake_media_classes):
        """With an audio_track_override, ffprobe (and ffprobe_cmd) is never invoked."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream") as mock_find,
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(
                qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2, ffprobe_cmd="/custom/ffprobe"
            )
            mock_find.assert_not_called()
            assert widget._jp_audio_index == 2

    def test_set_source_connects_tracks_changed(self, qtbot, fake_media_classes):
        """set_source should connect the tracksChanged signal."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            fake_media_classes["player"].tracksChanged.connect.assert_called()

    def test_set_source_twice_stops_previous_player(self, qtbot, fake_media_classes):
        """Calling set_source a second time should stop the previous player."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            first_player = fake_media_classes["player"]
            # Reset mock to isolate second call
            first_player.reset_mock()
            _set_source_sync(qtbot, widget, Path("/tmp/other.mkv"), [], 0.0)
        first_player.stop.assert_called_once()

    def test_set_source_twice_fully_tears_down_first_player(self, qtbot):
        """Calling set_source a second time should disconnect signals, clear audio, and deleteLater on the first player + its audio output."""
        mock1 = MagicMock()
        mock1.audioTracks.return_value = []
        mock2 = MagicMock()
        mock2.audioTracks.return_value = []
        audio1 = MagicMock()
        audio2 = MagicMock()

        with (
            patch(f"{MODULE}.QMediaPlayer", side_effect=[mock1, mock2]),
            patch(f"{MODULE}.QAudioOutput", side_effect=[audio1, audio2]),
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            qtbot.waitUntil(lambda: widget.player is mock1)
            widget.set_source(Path("/tmp/other.mkv"), [], 0.0)
            qtbot.waitUntil(lambda: widget.player is mock2)

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
        # The first audio output must also be scheduled for deletion so it
        # doesn't accumulate under the widget per re-source (F9).
        audio1.deleteLater.assert_called_once()

    def test_set_source_with_audio_track_override(self, qtbot, fake_media_classes):
        """audio_track_override should skip ffprobe and use the given index."""
        with (
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                side_effect=AssertionError("ffprobe should not be called"),
            ),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
            assert widget._jp_audio_index == 2

    def test_set_source_default_offset_zero(self, qtbot, fake_media_classes):
        """Default offset should be 0.0 when not specified."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [])
            assert widget._offset == 0.0


class TestAsyncPlayAndProbeError:
    """m6: async set_source — queued play + probe-error feedback."""

    def test_play_queues_when_player_not_built(self, qtbot):
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        assert widget.player is None
        widget.play()
        assert widget._pending_play is True
        # An explicit pause cancels the queued auto-play.
        widget.pause()
        assert widget._pending_play is False

    def test_pending_play_honored_when_player_configured(self, qtbot, fake_media_classes):
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        # Simulate a play() that arrived while the probe was in flight.
        widget._pending_play = True
        widget._source_generation = 7
        widget._configure_player(7, Path("/tmp/fake.mkv"), (False, None))
        fake_media_classes["player"].play.assert_called_once()
        assert widget._pending_play is False

    def test_seek_queues_when_player_not_built(self, qtbot):
        """A seek issued before the player is built is remembered, not dropped."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        assert widget.player is None
        widget.seek_seconds(4.0)
        assert widget._pending_seek_ms == 4000
        # An explicit stop clears the queued seek.
        widget.stop()

    def test_pause_queues_when_player_not_built(self, qtbot):
        """A pause issued before the player is built is remembered, not dropped."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        assert widget.player is None
        widget.pause()
        assert widget._pending_pause is True
        # A subsequent play cancels the queued pause.
        widget.play()
        assert widget._pending_pause is False

    def test_pending_seek_and_pause_honored_when_player_configured(self, qtbot, fake_media_classes):
        """A seek + pause queued while the probe was in flight are applied on configure."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.seek_seconds(4.0)
        widget.pause()
        widget._source_generation = 7
        widget._configure_player(7, Path("/tmp/fake.mkv"), (False, None))
        fake_media_classes["player"].setPosition.assert_called_once_with(4000)
        fake_media_classes["player"].pause.assert_called_once()
        assert widget._pending_seek_ms is None
        assert widget._pending_pause is False

    def test_probe_error_surfaces_message(self, qtbot):
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        with patch(f"{MODULE}.get_primary_video_codec", side_effect=RuntimeError("bad probe")):
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            qtbot.waitUntil(lambda: widget.subtitle_label.text() != "", timeout=2000)
        assert widget.player is None
        assert "could not load" in widget.subtitle_label.text().lower()
        # isVisibleTo(parent) reflects the setVisible(True) even with no shown top-level.
        assert widget.subtitle_label.isVisibleTo(widget)


class TestSeekSeconds:
    """Tests for SubtitlePlayerWidget.seek_seconds."""

    def test_seek_seconds_calls_set_position(self, qtbot, fake_media_classes):
        """seek_seconds should call player.setPosition with ms value."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            widget.seek_seconds(5.0)
        fake_media_classes["player"].setPosition.assert_called_with(5000)

    def test_seek_seconds_clamps_negative_to_zero(self, qtbot, fake_media_classes):
        """Negative seek values should be clamped to 0."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            widget.seek_seconds(-3.0)
        fake_media_classes["player"].setPosition.assert_called_with(0)

    def test_seek_seconds_fractional(self, qtbot, fake_media_classes):
        """Fractional seconds should be converted to int ms."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
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
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            widget.play()
        fake_media_classes["player"].play.assert_called()

    def test_pause_delegates_to_player(self, qtbot, fake_media_classes):
        """pause() should call player.pause()."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            widget.pause()
        fake_media_classes["player"].pause.assert_called()

    def test_stop_delegates_to_player(self, qtbot, fake_media_classes):
        """stop() should call player.stop()."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
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
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player_cls = fake_media_classes["player_cls"]
        player.playbackState.return_value = player_cls.PlaybackState.PlayingState

        widget.toggle_play_pause()

        player.pause.assert_called_once()
        player.play.assert_not_called()

    def test_toggle_plays_when_not_playing(self, qtbot, fake_media_classes):
        """When paused/stopped, toggle should play."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
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
        with (
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                return_value=JapaneseAudioStream(global_index=2, audio_index=1, language_tag="jpn"),
            ),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(1)

    def test_on_tracks_changed_noop_when_no_japanese(self, qtbot, fake_media_classes):
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_on_tracks_changed_bounds_check_skips_out_of_range(self, qtbot, fake_media_classes):
        with (
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                return_value=JapaneseAudioStream(global_index=5, audio_index=3, language_tag="jpn"),
            ),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_override_skips_ffprobe(self, qtbot, fake_media_classes):
        """When audio_track_override is set, ffprobe should not be called."""
        with (
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                side_effect=AssertionError("ffprobe should not be called"),
            ),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
            assert widget._jp_audio_index == 2

    def test_override_used_in_on_tracks_changed(self, qtbot, fake_media_classes):
        """Override index should be passed to setActiveAudioTrack."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", side_effect=AssertionError("should not call ffprobe")),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock(), MagicMock()]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_called_once_with(2)

    def test_qt_metadata_fallback_finds_japanese(self, qtbot, fake_media_classes):
        """When ffprobe returns None and no override, Qt metadata should find Japanese track."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
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
        with (
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                return_value=JapaneseAudioStream(global_index=0, audio_index=0, language_tag="jpn"),
            ),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
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
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
        player = fake_media_classes["player"]

        track_en = MagicMock()
        track_en.value.return_value = QLocale.Language.English
        player.audioTracks.return_value = [track_en, track_en]

        widget._on_tracks_changed()

        player.setActiveAudioTrack.assert_not_called()

    def test_override_index_zero_selects_first_track(self, qtbot, fake_media_classes):
        """audio_track_override=0 is a valid first-track index."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream") as mock_find_jp,
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=0)
            mock_find_jp.assert_not_called()
            assert widget._jp_audio_index == 0

        player = fake_media_classes["player"]
        player.audioTracks.return_value = [MagicMock(), MagicMock()]
        widget._on_tracks_changed()
        player.setActiveAudioTrack.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# OVH-057 — closeEvent + _teardown_player
# ---------------------------------------------------------------------------


class TestCloseEventTeardown:
    """OVH-057: closeEvent must call _teardown_player so the multimedia backend
    is released deterministically when the widget (or its dialog parent) is
    discarded — not at Python GC time."""

    def test_close_event_stops_player_and_detaches_audio(self, qtbot, fake_media_classes):
        """closeEvent calls stop() and setAudioOutput(None) on the active player."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        player = fake_media_classes["player"]
        player.reset_mock()

        # Simulate closeEvent directly (avoids triggering Qt window close).
        from PyQt6.QtGui import QCloseEvent

        widget.closeEvent(QCloseEvent())

        player.stop.assert_called_once()
        player.setAudioOutput.assert_any_call(None)

    def test_close_event_without_player_does_not_raise(self, qtbot):
        """closeEvent before set_source (no player) must be a no-op."""
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        assert widget.player is None

        from PyQt6.QtGui import QCloseEvent

        widget.closeEvent(QCloseEvent())  # must not raise

    def test_close_event_clears_player_reference(self, qtbot, fake_media_classes):
        """After closeEvent, widget.player must be None."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        from PyQt6.QtGui import QCloseEvent

        widget.closeEvent(QCloseEvent())
        assert widget.player is None
        assert widget.audio_output is None

    def test_teardown_player_is_idempotent(self, qtbot, fake_media_classes):
        """Calling _teardown_player twice must not raise."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        widget._teardown_player()
        widget._teardown_player()  # second call: player is None, must be no-op

    def test_release_detaches_video_output(self, qtbot, fake_media_classes):
        """release() must detach the video sink with setVideoOutput(None).

        Windows GUI-freeze fix: destroying a QMediaPlayer whose D3D video sink is
        still attached hangs the GUI thread on the Qt6 FFmpeg backend. release() is
        the path both real consumers use (SubtitleViewer accept/reject/closeEvent
        and WordCurationDialog._stop_player). assert_any_call(None) — NOT
        assert_called_once — because a configured player is also passed the video
        widget at build time (_configure_player).
        """
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        player = fake_media_classes["player"]
        player.reset_mock()

        widget.release()

        player.setVideoOutput.assert_any_call(None)

    def test_teardown_detaches_video_after_stop_before_deletelater(self, qtbot, fake_media_classes):
        """The video-sink detach must land after stop() and before deleteLater().

        Ordering is load-bearing: detaching from a *stopped* player is the safe
        variant (never mid-PlayingState), and the sink must be gone before the
        player is destroyed. Keys on the specific setVideoOutput(None) call so the
        build-time setVideoOutput(video_widget) can't be matched by mistake.
        """
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        player = fake_media_classes["player"]
        player.reset_mock()

        widget.release()

        names = player.mock_calls
        stop_idx = names.index(call.stop())
        detach_idx = names.index(call.setVideoOutput(None))
        delete_idx = names.index(call.deleteLater())
        assert stop_idx < detach_idx < delete_idx

    def test_teardown_drains_deferred_delete_when_debug(self, qtbot, fake_media_classes, caplog):
        """Under DEBUG, teardown forces the player's queued C++ dtor to run now.

        deleteLater() only queues the destruction (serviced by the outer event
        loop after exec() returns); the targeted sendPostedEvents drain pulls it
        into the timed window so a destruction-time Windows hang is observable
        here. Diagnostic-only and DEBUG-gated.
        """
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        player = fake_media_classes["player"]

        with (
            patch(f"{MODULE}.QCoreApplication") as fake_qca,
            caplog.at_level(logging.DEBUG, logger=MODULE),
        ):
            widget.release()

        fake_qca.sendPostedEvents.assert_called_once_with(player, QEvent.Type.DeferredDelete.value)

    def test_teardown_does_not_drain_when_debug_off(self, qtbot, fake_media_classes):
        """With DEBUG off, the drain never runs — so sendPostedEvents (which would
        raise TypeError on a mocked player) is never reached. Self-validating: it
        asserts the gate is actually closed, so a future DEBUG-default regression
        fails THIS test loudly instead of an opaque TypeError storm.
        """
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

        # Precondition: the DEBUG gate must be closed in the default test config.
        assert not logging.getLogger(MODULE).isEnabledFor(logging.DEBUG)

        with patch(f"{MODULE}.QCoreApplication") as fake_qca:
            widget.release()

        fake_qca.sendPostedEvents.assert_not_called()

    def test_player_parented_to_widget_at_construction(self, qtbot):
        """QMediaPlayer and QAudioOutput must be parented to the widget (not None)
        so Qt's object tree frees them when the widget is deleted.

        This test patches the constructors and inspects the ``parent`` positional
        argument (first positional arg after ``self`` in Qt constructors).
        """
        player_instance = MagicMock()
        player_instance.audioTracks.return_value = []
        audio_instance = MagicMock()

        with (
            patch(f"{MODULE}.QMediaPlayer") as player_cls,
            patch(f"{MODULE}.QAudioOutput") as audio_cls,
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            player_cls.return_value = player_instance
            audio_cls.return_value = audio_instance

            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)

            # Both must be constructed with the widget as their Qt parent.
            player_cls.assert_called_once_with(widget)
            audio_cls.assert_called_once_with(widget)

    def test_override_logs_in_first_branch_not_qt_branch(self, qtbot, fake_media_classes, caplog):
        """Override path should log 'Selected audio track', not 'Qt metadata'."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", side_effect=AssertionError("should not call ffprobe")),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=1)
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
    the watchdog window after LoadedMedia, the widget hides the video area
    and shows a fallback notice instead.
    """

    def test_av1_creates_player(self, qtbot, fake_media_classes):
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/av1.mkv"), [], 0.0)
            fake_media_classes["player_cls"].assert_called_once()
            assert widget.player is not None

    def test_supported_codec_creates_player(self, qtbot, fake_media_classes):
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="h264"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
            fake_media_classes["player_cls"].assert_called_once()
            assert widget.player is not None


class TestAv1WatchdogFallback:
    """Tests for the AV1 first-decoded-frame watchdog and fallback UI.

    The watchdog arms on LoadedMedia/BufferedMedia when the source is AV1 and no
    video frame has arrived yet.  If no frame arrives within the deadline, the
    timeout handler hides the video widget and shows the fallback notice.  A frame
    arriving before the timeout cancels the watchdog and keeps normal playback UI
    visible.  Non-AV1 sources never arm the watchdog.
    """

    def _make_widget_av1(self, qtbot, fake_media_classes, *, show=True):
        """Helper: build a widget with an AV1 source loaded (watchdog NOT yet armed).

        ``show`` defaults to True because the watchdog only arms on LoadedMedia
        when the widget is on screen (Issue #82); pass ``show=False`` to exercise
        the hidden/deferred path.
        """
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            if show:
                widget.show()
                qtbot.waitUntil(widget.isVisible)
            _set_source_sync(qtbot, widget, Path("/tmp/av1.mkv"), [], 0.0)
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

        # Fire the watchdog directly (avoids the real timeout wait).
        widget._on_av1_watchdog_timeout()

        # Use isHidden() — the widget isn't show()n, so isVisible() requires parent to be shown.
        assert widget.video_widget.isHidden(), "video_widget should be hidden after fallback"
        assert not widget._av1_notice_label.isHidden(), "fallback notice should be visible"

    def test_watchdog_fires_keeps_player_for_audio(self, qtbot, fake_media_classes):
        """When the watchdog fires, the player is NOT stopped — audio keeps playing."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        fake_media_classes["player"].reset_mock()
        widget._on_av1_watchdog_timeout()

        fake_media_classes["player"].stop.assert_not_called()

    def test_watchdog_fallback_keeps_subtitles_updating(self, qtbot, fake_media_classes):
        """After the fallback, subtitle overlay still updates from playback position.

        The video is gone but audio plays on, so the subtitle label must track
        position — that's what lets the user verify audio/subtitle sync.
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        widget.subtitle_entries = [(1.0, 3.0, "テスト")]
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        widget._on_av1_watchdog_timeout()
        assert widget.video_widget.isHidden()

        fake_media_classes["player"].duration.return_value = 10000
        widget._on_position_changed(2000)  # 2.0 s — inside [1.0, 3.0]

        assert widget.subtitle_label.text() == "テスト"
        assert not widget.subtitle_label.isHidden()

    def test_av1_notice_mentions_audio_subtitles(self, qtbot, fake_media_classes):
        """The fallback notice keeps the AV1 message and states audio/subtitles play."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        text = widget._av1_notice_label.text()
        assert "AV1" in text
        assert "Audio and subtitles still play" in text

    def test_late_frame_after_fallback_restores_video_and_resumes(self, qtbot, fake_media_classes):
        """A frame decoded AFTER the watchdog fired (slow software decode) undoes
        the fallback: notice hidden, video restored, playback resumed."""
        widget = self._make_widget_av1(qtbot, fake_media_classes)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        widget._on_av1_watchdog_timeout()
        # Fallback is showing, audio still playing.
        assert widget.video_widget.isHidden()
        assert not widget._av1_notice_label.isHidden()

        fake_media_classes["player"].reset_mock()
        widget._on_video_frame_changed()  # late frame arrives

        assert not widget.video_widget.isHidden(), "video should be restored"
        assert widget._av1_notice_label.isHidden(), "fallback notice should be hidden again"
        fake_media_classes["player"].play.assert_called_once()

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
            _set_source_sync(qtbot, widget, Path("/tmp/h264.mkv"), [], 0.0)

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
            _set_source_sync(qtbot, widget, Path("/tmp/hevc.mkv"), [], 0.0)

        # Watchdog is never armed for non-AV1 sources.
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert not widget._av1_watchdog.isActive()

        # Even a direct call to the timeout handler must be a no-op when _is_av1 is False.
        widget._on_av1_watchdog_timeout()
        assert not widget.video_widget.isHidden(), "video_widget must stay visible for non-AV1"
        assert widget._av1_notice_label.isHidden(), "fallback notice must remain hidden for non-AV1"

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
            _set_source_sync(qtbot, widget, Path("/tmp/h264.mkv"), [], 0.0)

        # Use isHidden() — the widget isn't show()n, so isVisible() requires parent to be shown.
        assert not widget.video_widget.isHidden(), "video_widget should be restored on new source"
        assert widget._av1_notice_label.isHidden(), "fallback notice should be hidden on new source"

    # ------------------------------------------------------------------
    # 5. Visibility gating (Issue #82): LoadedMedia while hidden must not arm
    # ------------------------------------------------------------------

    def test_loaded_media_while_hidden_does_not_arm(self, qtbot, fake_media_classes):
        """LoadedMedia firing before the dialog is shown must NOT arm the watchdog.

        Both callers set_source during dialog construction, before .exec() shows
        the window. Nudging/arming the decode clock while hidden would seek an
        off-screen sink and expire before any frame — the Issue #82 false fallback.
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=False)

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        assert not widget._av1_watchdog.isActive(), "Watchdog must not arm while hidden"
        assert widget._av1_watchdog_pending, "Arming should be deferred to showEvent"
        assert not widget.video_widget.isHidden(), "no fallback while hidden"
        assert widget._av1_notice_label.isHidden(), "fallback notice must stay hidden"

    def test_show_arms_deferred_watchdog(self, qtbot, fake_media_classes):
        """Once the widget is shown, the deferred watchdog arms."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=False)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert not widget._av1_watchdog.isActive()

        widget.show()
        qtbot.waitUntil(widget.isVisible)

        assert widget._av1_watchdog.isActive(), "showEvent should arm the deferred watchdog"
        assert not widget._av1_watchdog_pending, "pending flag cleared after arming"

    def test_loaded_media_while_visible_arms_immediately(self, qtbot, fake_media_classes):
        """When the widget is already on screen, LoadedMedia arms right away."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        assert widget._av1_watchdog.isActive(), "visible widget should arm immediately"
        assert not widget._av1_watchdog_pending

    def test_frame_before_show_keeps_watchdog_disarmed(self, qtbot, fake_media_classes):
        """A frame decoded before the widget is shown must keep the watchdog disarmed.

        showEvent must not arm once a frame has already arrived.
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=False)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        widget._on_video_frame_changed()  # frame arrives while still hidden

        widget.show()
        qtbot.waitUntil(widget.isVisible)

        assert not widget._av1_watchdog.isActive(), "no arming after a frame already decoded"
        assert not widget.video_widget.isHidden()
        assert widget._av1_notice_label.isHidden()

    def test_set_source_clears_pending_flag(self, qtbot, fake_media_classes):
        """Re-sourcing resets the deferred-arming flag."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=False)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert widget._av1_watchdog_pending

        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="h264"),
        ):
            _set_source_sync(qtbot, widget, Path("/tmp/h264.mkv"), [], 0.0)

        assert not widget._av1_watchdog_pending, "set_source must reset the pending flag"

    # ------------------------------------------------------------------
    # 6. Decode nudge (Issue #82): a frame must be *requested*, not awaited
    # ------------------------------------------------------------------

    def test_loaded_media_visible_nudges_first_frame(self, qtbot, fake_media_classes):
        """Arming on a visible AV1 source seeks to force the first frame to decode.

        set_source leaves the player stopped at 0; Qt's hardware-AV1 path presents
        no frame until a decode is requested, so the watchdog must nudge a seek
        (Issue #82) rather than wait for a frame nobody asked for.
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)
        player = fake_media_classes["player"]
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        # No subtitle entries in the helper, so the nudge uses the fallback position.
        player.setPosition.assert_called_once_with(_AV1_NUDGE_FALLBACK_MS)
        # pause() is load-bearing: it transitions StoppedState -> PausedState, which
        # drives the decode-and-present a bare seek on a never-played player may not.
        player.pause.assert_called_once()
        assert widget._av1_watchdog.isActive(), "watchdog armed alongside the nudge"

    def test_nudge_seeks_to_first_subtitle_timestamp(self, qtbot, fake_media_classes):
        """With subtitle entries the nudge seeks to the first entry's start (Issue #82).

        A real (non-zero) keyframe seek forces a genuine demux+decode; a near-zero
        seek coalesces to frame 0 and decodes nothing.
        """
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.show()
            qtbot.waitUntil(widget.isVisible)
            # First subtitle starts at 12.5s -> nudge target 12500 ms.
            _set_source_sync(qtbot, widget, Path("/tmp/av1.mkv"), [(12.5, 15.0, "x")], 0.0)
        player = fake_media_classes["player"]
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        player.setPosition.assert_called_once_with(12500)
        player.pause.assert_called_once()

    def test_show_event_nudges_deferred_decode(self, qtbot, fake_media_classes):
        """A nudge deferred while hidden fires on showEvent, not before.

        Seeking an off-screen video sink presents nothing, so the nudge must wait
        until the widget is on screen (Issue #82).
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=False)
        player = fake_media_classes["player"]
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        player.setPosition.assert_not_called()  # hidden: no seek into an off-screen sink
        player.pause.assert_not_called()

        widget.show()
        qtbot.waitUntil(widget.isVisible)

        player.setPosition.assert_called_once_with(_AV1_NUDGE_FALLBACK_MS)
        player.pause.assert_called_once()
        assert widget._av1_watchdog.isActive()

    def test_non_av1_does_not_nudge(self, qtbot, fake_media_classes):
        """Non-AV1 sources skip the nudge entirely."""
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="h264"),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.show()
            qtbot.waitUntil(widget.isVisible)
            _set_source_sync(qtbot, widget, Path("/tmp/h264.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        player.setPosition.assert_not_called()
        player.pause.assert_not_called()
        assert not widget._av1_watchdog.isActive()

    def test_nudge_skipped_once_frame_decoded(self, qtbot, fake_media_classes):
        """Once a frame has decoded, the nudge is a no-op — nothing left to force."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)
        player = fake_media_classes["player"]
        widget._got_video_frame = True
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._nudge_first_frame()

        player.setPosition.assert_not_called()
        player.pause.assert_not_called()

    def test_nudge_skipped_while_playing(self, qtbot, fake_media_classes):
        """The nudge must not seek/pause an actively-playing stream (Bug A2).

        A requested play would otherwise be cancelled and the position yanked to
        the first entry; a playing stream is already decoding so the nudge is
        unnecessary.
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)
        player = fake_media_classes["player"]
        player.playbackState.return_value = fake_media_classes["player_cls"].PlaybackState.PlayingState
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._nudge_first_frame()

        player.setPosition.assert_not_called()
        player.pause.assert_not_called()

    def test_arm_while_playing_skips_nudge_but_arms_watchdog(self, qtbot, fake_media_classes):
        """Even while playing, arming still starts the watchdog (only the nudge is skipped)."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)
        player = fake_media_classes["player"]
        player.playbackState.return_value = fake_media_classes["player_cls"].PlaybackState.PlayingState
        player.setPosition.reset_mock()
        player.pause.reset_mock()

        widget._arm_av1_decode_check()

        player.setPosition.assert_not_called()
        player.pause.assert_not_called()
        assert widget._av1_watchdog.isActive()

    def test_watchdog_uses_configured_timeout(self, qtbot, fake_media_classes):
        """The armed watchdog uses the cold-init-tolerant timeout constant (Issue #82)."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        assert widget._av1_watchdog.isActive()
        assert widget._av1_watchdog.interval() == _AV1_WATCHDOG_MS
        assert _AV1_WATCHDOG_MS >= 5000, "timeout must absorb GPU cold-init"

    def test_loaded_then_buffered_nudges_once(self, qtbot, fake_media_classes):
        """LoadedMedia then BufferedMedia must nudge + arm once, not re-seek/restart.

        mediaStatusChanged typically fires LoadedMedia then BufferedMedia for the
        same source; arming is idempotent so the second status change is a no-op
        while the watchdog is still pending a frame.
        """
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)
        player = fake_media_classes["player"]
        player.setPosition.reset_mock()

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)

        player.setPosition.assert_called_once_with(_AV1_NUDGE_FALLBACK_MS)
        assert widget._av1_watchdog.isActive()

    def test_resource_to_new_av1_renudges(self, qtbot, fake_media_classes):
        """A second AV1 source re-nudges: re-source resets state and forces a frame again."""
        widget = self._make_widget_av1(qtbot, fake_media_classes, show=True)
        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        # Re-source to another AV1 file; set_source resets _got_video_frame + watchdog.
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
        ):
            _set_source_sync(qtbot, widget, Path("/tmp/av1_second.mkv"), [], 0.0)
        player = fake_media_classes["player"]
        player.setPosition.reset_mock()

        widget._on_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)

        player.setPosition.assert_called_once_with(_AV1_NUDGE_FALLBACK_MS)
        assert widget._av1_watchdog.isActive(), "second AV1 source must re-arm the watchdog"


class TestSetSourceAsyncProbe:
    """The two ffprobe calls run off the GUI thread; the player is configured
    in a GUI-thread callback once the probe returns (GUI-freeze hardening)."""

    def test_probe_runs_off_gui_thread(self, qtbot, fake_media_classes):
        """get_primary_video_codec / find_japanese_audio_stream must NOT run on the
        GUI thread; the player is configured only after the probe completes."""
        main_thread_id = threading.get_ident()
        codec_thread: dict[str, int] = {}
        audio_thread: dict[str, int] = {}

        def fake_codec(video_file, ffprobe_cmd="ffprobe"):
            codec_thread["id"] = threading.get_ident()
            return "h264"

        def fake_find(video_file, ffprobe_cmd="ffprobe"):
            audio_thread["id"] = threading.get_ident()
            return None

        with (
            patch(f"{MODULE}.get_primary_video_codec", side_effect=fake_codec),
            patch(f"{MODULE}.find_japanese_audio_stream", side_effect=fake_find),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            # The probe has NOT run synchronously: no player yet on return.
            assert widget.player is None
            qtbot.waitUntil(lambda: widget.player is not None, timeout=2000)

        assert codec_thread["id"] != main_thread_id, "codec probe ran on the GUI thread"
        assert audio_thread["id"] != main_thread_id, "audio probe ran on the GUI thread"
        assert codec_thread["id"] == audio_thread["id"], "both probes share one worker thread"

    def test_player_configured_after_probe(self, qtbot, fake_media_classes):
        """After the probe completes, _is_av1 / _jp_audio_index reflect its result."""
        with (
            patch(f"{MODULE}.get_primary_video_codec", return_value="av1"),
            patch(
                f"{MODULE}.find_japanese_audio_stream",
                return_value=JapaneseAudioStream(global_index=3, audio_index=2, language_tag="jpn"),
            ),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/av1.mkv"), [], 0.0)
            qtbot.waitUntil(lambda: widget.player is not None, timeout=2000)
            assert widget._is_av1 is True
            assert widget._jp_audio_index == 2

    def test_second_set_source_supersedes_first(self, qtbot, fake_media_classes):
        """A second set_source before the first probe finishes: only the latest
        source configures the player (generation guard); no crash."""
        first_started = threading.Event()
        release_first = threading.Event()

        def slow_first_codec(video_file, ffprobe_cmd="ffprobe"):
            # Only the first source's path blocks; the second returns immediately.
            if str(video_file) == "/tmp/first.mkv":
                first_started.set()
                release_first.wait(timeout=5)
                return "av1"
            return "h264"

        with (
            patch(f"{MODULE}.get_primary_video_codec", side_effect=slow_first_codec),
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)

            widget.set_source(Path("/tmp/first.mkv"), [], 0.0)
            assert first_started.wait(timeout=5), "first probe never started"
            first_gen = widget._source_generation

            # Second set_source while the first probe is blocked. _teardown_player
            # joins the first worker, so release it from another thread first.
            import threading as _t

            _t.Thread(target=release_first.set, daemon=True).start()
            widget.set_source(Path("/tmp/second.mkv"), [], 0.0)

            assert widget._source_generation > first_gen
            qtbot.waitUntil(lambda: widget.player is not None, timeout=2000)

        # The configured player reflects the SECOND source (h264, not av1).
        assert widget._is_av1 is False, "the superseded first probe must not configure the player"

    def test_probe_failure_leaves_player_none_and_logs(self, qtbot, fake_media_classes, caplog):
        """A raising probe leaves the widget player-less and logs; no exception."""
        with (
            patch(f"{MODULE}.get_primary_video_codec", side_effect=RuntimeError("boom")),
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            with caplog.at_level(logging.WARNING):
                widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
                # Give the worker time to fail and the error slot to run.
                qtbot.wait(300)

        assert widget.player is None, "a failed probe must not build a player"
        assert any("ffprobe failed" in r.message or "off-thread work failed" in r.message for r in caplog.records)

    def test_close_event_during_in_flight_probe_does_not_crash(self, qtbot, fake_media_classes):
        """closeEvent with a probe still running cancels/joins it without crashing."""
        from PyQt6.QtGui import QCloseEvent

        started = threading.Event()
        release = threading.Event()

        def blocking_codec(video_file, ffprobe_cmd="ffprobe"):
            started.set()
            release.wait(timeout=5)
            return "h264"

        with (
            patch(f"{MODULE}.get_primary_video_codec", side_effect=blocking_codec),
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            assert started.wait(timeout=5), "probe never started"

            # Release from another thread so closeEvent's bounded join completes.
            import threading as _t

            _t.Thread(target=release.set, daemon=True).start()
            widget.closeEvent(QCloseEvent())  # must not crash

        assert widget.player is None

    def test_close_event_detaches_stuck_probe_laggard(self, qtbot, fake_media_classes):
        """A probe stuck past the teardown join is detached from the dying widget.

        ``SingleCallWorker.cancel()`` cannot interrupt a blocking ffprobe, so a
        genuinely stuck probe stays running through the short join. closeEvent must
        not crash and must ``setParent(None)`` the laggard so Qt never destroys a
        running QThread (which would abort the process). The worker stays tracked
        on the widget (keeping its Python wrapper alive) until it finishes. We
        release the block and join at the end so the thread does not leak.
        """
        from PyQt6.QtGui import QCloseEvent

        started = threading.Event()
        release = threading.Event()

        def stuck_codec(video_file, ffprobe_cmd="ffprobe"):
            started.set()
            release.wait(timeout=10)  # stays blocked across the teardown join
            return "h264"

        with (
            patch(f"{MODULE}.get_primary_video_codec", side_effect=stuck_codec),
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            widget.set_source(Path("/tmp/fake.mkv"), [], 0.0)
            assert started.wait(timeout=5), "probe never started"

            worker = widget._probe_worker
            assert worker is not None

            # Tear down WITHOUT releasing the block: the probe is genuinely stuck,
            # so the short join times out and the worker is returned as a laggard.
            widget.closeEvent(QCloseEvent())  # must not crash

            # Laggard detached from the dying widget, but still tracked so its
            # Python wrapper survives until it self-cleans on finished.
            assert worker.parent() is None, "stuck probe was not detached from the widget"
            assert worker.isRunning(), "the stuck probe should still be running after teardown"
            assert worker in widget._off_thread_workers, "laggard must stay tracked until it finishes"
            assert widget.player is None

            # Release + join so the thread does not leak out of the test.
            release.set()
            assert worker.wait(5000), "detached probe never finished after release"


class TestPositionSliderScrubGuard:
    """Bug A4: playback position updates must not fight a user scrubbing the slider."""

    def _make_widget(self, qtbot, fake_media_classes):
        with (
            patch(f"{MODULE}.find_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.get_primary_video_codec", return_value=None),
        ):
            widget = SubtitlePlayerWidget()
            qtbot.addWidget(widget)
            _set_source_sync(qtbot, widget, Path("/tmp/fake.mkv"), [], 0.0)
        fake_media_classes["player"].duration.return_value = 10000
        widget.position_slider.setRange(0, 10000)
        return widget

    def test_position_change_ignored_while_slider_down(self, qtbot, fake_media_classes):
        """While the handle is held, a playback position update must not move it."""
        widget = self._make_widget(qtbot, fake_media_classes)
        widget.position_slider.setValue(1000)
        widget.position_slider.setSliderDown(True)

        widget._on_position_changed(5000)

        assert widget.position_slider.value() == 1000

    def test_position_change_applied_when_slider_not_down(self, qtbot, fake_media_classes):
        """With the handle released, a playback position update moves the slider normally."""
        widget = self._make_widget(qtbot, fake_media_classes)
        widget.position_slider.setValue(1000)
        widget.position_slider.setSliderDown(False)

        widget._on_position_changed(5000)

        assert widget.position_slider.value() == 5000

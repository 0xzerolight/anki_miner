"""Reusable video player widget with subtitle overlay."""

import logging
from pathlib import Path

from PyQt6.QtCore import QLocale, Qt, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.utils import find_japanese_audio_stream

logger = logging.getLogger(__name__)

# Base subtitle-overlay font size (px) at scale 1.0.
_BASE_OVERLAY_FONT_PX = 18


class SubtitlePlayerWidget(QWidget):
    """Reusable video player with subtitle overlay, extracted from SubtitleViewer.

    Owns QVideoWidget, overlay QLabel, position QSlider, time label, play/pause button,
    QMediaPlayer, and QAudioOutput. No player is created until set_source is called.
    """

    def __init__(self, parent=None):
        """Initialize the player widget (no media until set_source is called).

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)

        # Will be populated by set_source
        self.subtitle_entries: list[tuple[float, float, str]] = []
        self._offset: float = 0.0
        self._jp_audio_index: int | None = None
        self._audio_track_override: int | None = None

        # Player is None until set_source is called
        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the player UI layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video widget
        self.video_widget = QVideoWidget()
        layout.addWidget(self.video_widget, 1)

        # Subtitle overlay label
        self.subtitle_label = QLabel()
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        # Overlay font is set at construction, reflecting the scale at that time.
        # The player widget is created per session/playback, so this is fine —
        # no live re-scaling plumbing (YAGNI).
        overlay_font_px = max(1, round(_BASE_OVERLAY_FONT_PX * Theme.get_font_scale()))
        self.subtitle_label.setStyleSheet(
            "QLabel { background-color: rgba(0,0,0,180); color: white; "
            f"font-size: {overlay_font_px}px; padding: 6px 12px; border-radius: 4px; }}"
        )
        self.subtitle_label.setVisible(False)
        layout.addWidget(self.subtitle_label)

        # Position slider and time display
        position_layout = QHBoxLayout()
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(100)

        position_layout.addWidget(self.position_slider, 1)
        position_layout.addWidget(self.time_label)
        layout.addLayout(position_layout)

        # Play/pause button row
        controls_layout = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setFixedWidth(80)
        self.play_button.clicked.connect(self.toggle_play_pause)
        controls_layout.addWidget(self.play_button)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        self.setLayout(layout)

    def set_source(
        self,
        video_path: Path,
        subtitle_entries: list[tuple[float, float, str]],
        offset: float = 0.0,
        *,
        audio_track_override: int | None = None,
        ffprobe_cmd: str = "ffprobe",
    ) -> None:
        """Load a video source and configure subtitle playback.

        If called again on an already-loaded source, the previous player is stopped first.

        Args:
            video_path: Path to the video file.
            subtitle_entries: List of (start_seconds, end_seconds, text) tuples.
            offset: Initial subtitle timing offset in seconds.
            audio_track_override: Optional 0-indexed audio track to force instead of
                auto-detecting Japanese. None preserves auto-detect.
            ffprobe_cmd: ffprobe executable path/literal used for audio-track auto-detection.
                Defaults to the bare ``"ffprobe"`` literal (PATH lookup).
        """
        # Stop and fully tear down any existing player before re-initialising
        if self.player is not None:
            self.player.stop()
            self.player.positionChanged.disconnect(self._on_position_changed)
            self.player.durationChanged.disconnect(self._on_duration_changed)
            self.player.playbackStateChanged.disconnect(self._on_playback_state_changed)
            self.player.errorOccurred.disconnect(self._on_media_error)
            self.player.tracksChanged.disconnect(self._on_tracks_changed)
            self.player.setAudioOutput(None)
            self.player.deleteLater()
            self.player = None
            self.audio_output = None

        self.subtitle_entries = subtitle_entries
        self._offset = offset
        self._audio_track_override = audio_track_override

        if audio_track_override is None:
            jp_stream = find_japanese_audio_stream(video_path, ffprobe_cmd=ffprobe_cmd)
            self._jp_audio_index = jp_stream.audio_index if jp_stream is not None else None
        else:
            self._jp_audio_index = audio_track_override

        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_media_error)
        self.player.tracksChanged.connect(self._on_tracks_changed)

        self.player.setSource(QUrl.fromLocalFile(str(video_path)))

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def seek_seconds(self, seconds: float) -> None:
        """Seek to an absolute position.

        Args:
            seconds: Target position in seconds (clamped to >= 0).
        """
        if self.player is not None:
            self.player.setPosition(max(0, int(seconds * 1000)))

    def set_offset(self, offset: float) -> None:
        """Update the subtitle timing offset (overlay sync only).

        Args:
            offset: New offset in seconds.
        """
        self._offset = offset

    def play(self) -> None:
        """Start playback."""
        if self.player is not None:
            self.player.play()

    def pause(self) -> None:
        """Pause playback."""
        if self.player is not None:
            self.player.pause()

    def stop(self) -> None:
        """Stop playback (no-op if no source has been loaded)."""
        if self.player is not None:
            self.player.stop()

    def toggle_play_pause(self) -> None:
        """Toggle play/pause (no-op if no source has been loaded)."""
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    # ------------------------------------------------------------------
    # Internal signal handlers
    # ------------------------------------------------------------------

    def _on_tracks_changed(self) -> None:
        """Select the Japanese audio track once QMediaPlayer enumerates tracks."""
        if self.player is None:  # safety: signal only fires after set_source
            return
        if self._jp_audio_index is not None:
            # ffprobe found JP, or user gave an override — honor it
            track_count = len(self.player.audioTracks())
            if self._jp_audio_index >= track_count:
                logger.warning(
                    f"Audio track index {self._jp_audio_index} out of range "
                    f"(player reports {track_count} audio tracks)"
                )
                return
            self.player.setActiveAudioTrack(self._jp_audio_index)
            logger.info(f"Selected audio track {self._jp_audio_index} in mini-player")
            return

        # Both override and ffprobe returned nothing — try Qt-side language metadata.
        for i, track in enumerate(self.player.audioTracks()):
            lang = track.value(QMediaMetaData.Key.Language)
            if lang == QLocale.Language.Japanese:
                self.player.setActiveAudioTrack(i)
                logger.info(f"Selected Japanese audio track {i} via Qt metadata fallback")
                return
        # Qt also can't identify — leave QMediaPlayer's default (no action)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Update play button text based on playback state.

        Args:
            state: Current playback state.
        """
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
        else:
            self.play_button.setText("Play")

    def _on_media_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        """Handle media player errors by showing message in subtitle label.

        Args:
            error: QMediaPlayer.Error enum value.
            error_string: Human-readable error description.
        """
        self.subtitle_label.setText(f"Video error: {error_string}")
        self.subtitle_label.setVisible(True)

    def _on_position_changed(self, position: int) -> None:
        """Handle media position change.

        Args:
            position: Current position in milliseconds.
        """
        if self.player is None:  # safety: signal only fires after set_source
            return
        self.position_slider.setValue(position)

        duration = self.player.duration()
        self.time_label.setText(f"{self._format_time(position)} / {self._format_time(duration)}")

        current_seconds = position / 1000.0
        self._update_subtitle(current_seconds)

    def _on_duration_changed(self, duration: int) -> None:
        """Handle media duration change.

        Args:
            duration: Total duration in milliseconds.
        """
        self.position_slider.setRange(0, duration)

    def _on_slider_moved(self, position: int) -> None:
        """Handle slider manual move.

        Args:
            position: New position in milliseconds.
        """
        if self.player is not None:
            self.player.setPosition(position)

    def _update_subtitle(self, current_seconds: float) -> None:
        """Update the subtitle label based on current playback position.

        Args:
            current_seconds: Current playback position in seconds.
        """
        for start, end, text in self.subtitle_entries:
            adjusted_start = start + self._offset
            adjusted_end = end + self._offset
            if adjusted_start <= current_seconds <= adjusted_end:
                self.subtitle_label.setText(text)
                self.subtitle_label.setVisible(True)
                return

        self.subtitle_label.setVisible(False)

    @staticmethod
    def _format_time(ms: int) -> str:
        """Format milliseconds as MM:SS.

        Args:
            ms: Time in milliseconds.

        Returns:
            Formatted time string.
        """
        if ms < 0:
            ms = 0
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

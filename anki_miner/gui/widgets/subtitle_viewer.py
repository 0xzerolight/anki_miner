"""Subtitle timing adjustment viewer with video playback."""

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)


class SubtitleViewer(QDialog):
    """Dialog for previewing video with subtitles and adjusting timing offset.

    Plays the video file, overlays subtitle text based on timing, and allows
    the user to adjust a timing offset to correct sync issues.
    """

    def __init__(
        self,
        video_path: Path,
        subtitle_entries: list[tuple[float, float, str]],
        initial_offset: float = 0.0,
        parent=None,
    ):
        """Initialize the subtitle viewer.

        Args:
            video_path: Path to the video file
            subtitle_entries: List of (start_seconds, end_seconds, text) tuples
            initial_offset: Initial subtitle offset in seconds
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.subtitle_entries = subtitle_entries
        self._offset = initial_offset

        self.setWindowTitle("Subtitle Timing Viewer")
        self.setMinimumSize(720, 540)
        self.resize(800, 600)

        self._setup_ui()
        self._setup_media(video_path)

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Video widget
        self.video_widget = QVideoWidget()
        layout.addWidget(self.video_widget, 1)

        # Subtitle overlay label
        self.subtitle_label = QLabel()
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            "QLabel { background-color: rgba(0,0,0,180); color: white; "
            "font-size: 18px; padding: 6px 12px; border-radius: 4px; }"
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

        # Playback controls and offset
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)

        self.play_button = QPushButton("Play")
        self.play_button.setFixedWidth(80)
        self.play_button.clicked.connect(self._on_play_pause)
        controls_layout.addWidget(self.play_button)

        controls_layout.addStretch()

        # Offset control
        offset_label = QLabel("Offset:")
        controls_layout.addWidget(offset_label)

        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(-60.0, 60.0)
        self.offset_spinbox.setSingleStep(0.1)
        self.offset_spinbox.setValue(self._offset)
        self.offset_spinbox.setSuffix(" s")
        self.offset_spinbox.setToolTip("Positive = subtitles later, Negative = subtitles earlier")
        self.offset_spinbox.valueChanged.connect(self._on_offset_changed)
        controls_layout.addWidget(self.offset_spinbox)

        controls_layout.addStretch()

        # Apply / Cancel buttons
        apply_btn = QPushButton("Apply Offset")
        apply_btn.clicked.connect(self.accept)
        controls_layout.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        controls_layout.addWidget(cancel_btn)

        layout.addLayout(controls_layout)
        self.setLayout(layout)

    def _setup_media(self, video_path: Path) -> None:
        """Set up the media player.

        Args:
            video_path: Path to the video file
        """
        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setSource(QUrl.fromLocalFile(str(video_path)))

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_media_error)

    def _on_play_pause(self) -> None:
        """Toggle play/pause."""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Update play button text based on playback state.

        Args:
            state: Current playback state
        """
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
        else:
            self.play_button.setText("Play")

    def _on_media_error(self, error, error_string: str) -> None:
        """Handle media player errors by showing message in subtitle label.

        Args:
            error: QMediaPlayer.Error enum value
            error_string: Human-readable error description
        """
        self.subtitle_label.setText(f"Video error: {error_string}")
        self.subtitle_label.setVisible(True)

    def _on_position_changed(self, position: int) -> None:
        """Handle media position change.

        Args:
            position: Current position in milliseconds
        """
        self.position_slider.setValue(position)

        duration = self.player.duration()
        self.time_label.setText(f"{self._format_time(position)} / {self._format_time(duration)}")

        # Update subtitle display
        current_seconds = position / 1000.0
        self._update_subtitle(current_seconds)

    def _on_duration_changed(self, duration: int) -> None:
        """Handle media duration change.

        Args:
            duration: Total duration in milliseconds
        """
        self.position_slider.setRange(0, duration)

    def _on_slider_moved(self, position: int) -> None:
        """Handle slider manual move.

        Args:
            position: New position in milliseconds
        """
        self.player.setPosition(position)

    def _on_offset_changed(self, value: float) -> None:
        """Handle offset spinbox value change.

        Args:
            value: New offset value in seconds
        """
        self._offset = value

    def _update_subtitle(self, current_seconds: float) -> None:
        """Update the subtitle label based on current playback position.

        Args:
            current_seconds: Current playback position in seconds
        """
        for start, end, text in self.subtitle_entries:
            adjusted_start = start + self._offset
            adjusted_end = end + self._offset
            if adjusted_start <= current_seconds <= adjusted_end:
                self.subtitle_label.setText(text)
                self.subtitle_label.setVisible(True)
                return

        self.subtitle_label.setVisible(False)

    def get_offset(self) -> float:
        """Get the currently selected offset.

        Returns:
            Offset value in seconds
        """
        return self._offset

    @staticmethod
    def _format_time(ms: int) -> str:
        """Format milliseconds as MM:SS.

        Args:
            ms: Time in milliseconds

        Returns:
            Formatted time string
        """
        if ms < 0:
            ms = 0
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def closeEvent(self, event) -> None:
        """Stop media player on close."""
        self.player.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        """Stop media player on reject."""
        self.player.stop()
        super().reject()

    def accept(self) -> None:
        """Stop media player on accept."""
        self.player.stop()
        super().accept()

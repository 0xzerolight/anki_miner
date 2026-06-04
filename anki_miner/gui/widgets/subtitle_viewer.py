"""Subtitle timing adjustment viewer with video playback."""

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

logger = logging.getLogger(__name__)


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
        *,
        audio_track_override: int | None = None,
        ffprobe_cmd: str = "ffprobe",
    ):
        """Initialize the subtitle viewer.

        Args:
            video_path: Path to the video file
            subtitle_entries: List of (start_seconds, end_seconds, text) tuples
            initial_offset: Initial subtitle offset in seconds
            parent: Optional parent widget
            audio_track_override: Optional 0-indexed audio track to force instead of
                auto-detecting Japanese. None preserves auto-detect.
            ffprobe_cmd: ffprobe executable path/literal forwarded to the player for
                audio-track auto-detection. Defaults to the bare ``"ffprobe"`` literal.
        """
        super().__init__(parent)
        self._offset = initial_offset
        self._ffprobe_cmd = ffprobe_cmd

        self.setWindowTitle("Subtitle Timing Viewer")
        self.setMinimumSize(720, 540)
        self.resize(800, 600)

        self._setup_ui(initial_offset)
        self.player_widget.set_source(
            video_path,
            subtitle_entries,
            initial_offset,
            audio_track_override=audio_track_override,
            ffprobe_cmd=self._ffprobe_cmd,
        )

    def _setup_ui(self, initial_offset: float) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Embedded player widget
        self.player_widget = SubtitlePlayerWidget()
        layout.addWidget(self.player_widget, 1)

        # Playback controls and offset
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)

        controls_layout.addStretch()

        # Offset control
        offset_label = QLabel("Offset:")
        controls_layout.addWidget(offset_label)

        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        self.offset_spinbox.setSingleStep(0.1)
        self.offset_spinbox.setValue(initial_offset)
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

    def _on_offset_changed(self, value: float) -> None:
        """Handle offset spinbox value change.

        Args:
            value: New offset value in seconds
        """
        self._offset = value
        self.player_widget.set_offset(value)

    def get_offset(self) -> float:
        """Get the currently selected offset.

        Returns:
            Offset value in seconds
        """
        return self._offset

    def closeEvent(self, event) -> None:
        """Stop media player on close."""
        self.player_widget.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        """Stop media player on reject."""
        self.player_widget.stop()
        super().reject()

    def accept(self) -> None:
        """Stop media player on accept."""
        self.player_widget.stop()
        super().accept()

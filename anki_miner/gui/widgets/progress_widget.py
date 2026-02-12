"""Enhanced progress widget with gradients and rich statistics."""

from time import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from anki_miner.gui.constants import MIN_HEIGHT_PROGRESS_WIDGET
from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class ProgressWidget(QWidget):
    """Enhanced progress widget with rich statistics display.

    Features:
    - Gradient animated progress bar (styled via QSS)
    - Main status label showing current operation
    - Statistics bar with elapsed time, rate, and ETA
    - Support for both determinate and indeterminate modes
    """

    def __init__(self, parent=None):
        """Initialize the progress widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._start_time = None
        self._items_processed = 0
        self._total_items = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        # Set minimum height to prevent collapsing
        self.setMinimumHeight(MIN_HEIGHT_PROGRESS_WIDGET)

        # Main status label
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        font = QFont()
        font.setWeight(QFont.Weight.Medium)
        self.status_label.setFont(font)
        layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)

        # Statistics bar
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(SPACING.md)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("progress-stats")
        stats_font = QFont("Consolas")
        stats_font.setStyleHint(QFont.StyleHint.Monospace)
        stats_font.setPixelSize(FONT_SIZES.caption)
        self.stats_label.setFont(stats_font)

        stats_layout.addWidget(self.stats_label)
        stats_layout.addStretch()

        layout.addLayout(stats_layout)

        self.setLayout(layout)

        # Set size policy to prevent compression
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_progress(self, current: int, total: int, description: str = "") -> None:
        """Set progress value and update status with statistics.

        Args:
            current: Current progress value (1-based)
            total: Maximum progress value
            description: Optional description text
        """
        self._items_processed = current
        self._total_items = total

        # Start timer on first progress update
        if self._start_time is None and current > 0:
            self._start_time = time()

        if total > 0:
            # Calculate percentage
            percentage = int((current / total) * 100)
            self.progress_bar.setValue(percentage)
            self.progress_bar.setFormat(f"{current}/{total}")

        if description:
            self.status_label.setText(description)

        # Update statistics
        self._update_stats()

    def set_status(self, message: str) -> None:
        """Set the status message.

        Args:
            message: Status message to display
        """
        self.status_label.setText(message)

    def set_maximum(self, maximum: int) -> None:
        """Set the maximum progress value.

        Args:
            maximum: Maximum value for progress
        """
        self._total_items = maximum
        self.progress_bar.setMaximum(maximum)

    def set_value(self, value: int) -> None:
        """Set the current progress value.

        Args:
            value: Current progress value
        """
        self._items_processed = value
        self.progress_bar.setValue(value)
        self._update_stats()

    @property
    def total(self) -> int:
        """Get the total number of items."""
        return self._total_items

    def reset(self) -> None:
        """Reset progress to initial state."""
        self.progress_bar.setValue(0)
        self.status_label.setText("Ready")
        self.stats_label.setText("")
        self._start_time = None
        self._items_processed = 0
        self._total_items = 0

    def set_indeterminate(self) -> None:
        """Set progress bar to indeterminate mode (busy indicator)."""
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0)
        self.progress_bar.setFormat("Processing...")
        self._start_time = None

    def set_determinate(self, maximum: int = 100) -> None:
        """Set progress bar to determinate mode with specified maximum.

        Args:
            maximum: Maximum progress value (default: 100)
        """
        self._total_items = maximum
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._start_time = None
        self._items_processed = 0

    def _update_stats(self) -> None:
        """Update the statistics label with elapsed time and rate."""
        if self._start_time is None or self._items_processed == 0:
            self.stats_label.setText("")
            return

        elapsed = time() - self._start_time

        # Format elapsed time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        elapsed_str = f"{minutes:02d}:{seconds:02d}"

        # Calculate rate (items per second)
        rate = self._items_processed / elapsed if elapsed > 0 else 0

        # Build stats string
        time_icon = IconProvider.get_icon("time")
        rate_icon = IconProvider.get_icon("progress")

        stats_parts = [f"{time_icon} {elapsed_str}", f"{rate_icon} {rate:.1f}/sec"]

        # Calculate ETA if we have total
        if self._total_items > 0 and rate > 0:
            remaining = self._total_items - self._items_processed
            eta_seconds = remaining / rate
            eta_minutes = int(eta_seconds // 60)
            eta_secs = int(eta_seconds % 60)

            if eta_minutes > 0:
                stats_parts.append(f"ETA ~{eta_minutes:02d}:{eta_secs:02d}")

        self.stats_label.setText(" | ".join(stats_parts))

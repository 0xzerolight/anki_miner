"""Stat card widget for displaying metrics."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class StatCard(QFrame):
    """Card widget for displaying a single metric/statistic.

    Features:
    - Large value display
    - Small label
    - Card styling with border and shadow

    Typical usage: Display processing results like cards created, words discovered, etc.
    """

    def __init__(self, value: str = "0", label: str = "", parent=None):
        """Initialize the stat card.

        Args:
            value: Value to display (as string to support formatted numbers)
            label: Label text describing the metric
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._value = value
        self._label = label

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # Use a frame for card styling
        self.setObjectName("stat-card")

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(SPACING.xs)

        # Value (large, bold)
        self.value_label = QLabel(self._value)
        self.value_label.setObjectName("stat-value")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_font = QFont()
        value_font.setPixelSize(FONT_SIZES.stat_value)
        value_font.setWeight(QFont.Weight.Bold)
        self.value_label.setFont(value_font)
        layout.addWidget(self.value_label)

        # Label (small, uppercase)
        self.label_widget = QLabel(self._label.upper())
        self.label_widget.setObjectName("stat-label")
        self.label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label_font = QFont()
        label_font.setPixelSize(FONT_SIZES.caption)
        label_font.setWeight(QFont.Weight.Medium)
        self.label_widget.setFont(label_font)
        layout.addWidget(self.label_widget)

        self.setLayout(layout)

    def set_value(self, value: str) -> None:
        """Update the displayed value.

        Args:
            value: New value to display
        """
        self._value = value
        self.value_label.setText(value)

"""Stat card widget for displaying metrics."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class StatCard(QFrame):
    """Card widget for displaying a single metric/statistic.

    Features:
    - Large icon
    - Large value display (with optional animated counting)
    - Small label
    - Card styling with border and shadow
    - Optional trend indicator

    Typical usage: Display processing results like cards created, words discovered, etc.
    """

    def __init__(self, icon: str = "", value: str = "0", label: str = "", parent=None):
        """Initialize the stat card.

        Args:
            icon: Icon name from IconProvider
            value: Value to display (as string to support formatted numbers)
            label: Label text describing the metric
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._icon = icon
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

        # Icon (large emoji) — only create label if icon text is non-empty
        icon_text = IconProvider.get_icon(self._icon) if self._icon else ""
        if icon_text:
            self.icon_label = QLabel(icon_text)
            icon_font = QFont()
            icon_font.setPixelSize(FONT_SIZES.stat_value)
            self.icon_label.setFont(icon_font)
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.icon_label)

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

    def set_label(self, label: str) -> None:
        """Update the label text.

        Args:
            label: New label text
        """
        self._label = label
        self.label_widget.setText(label.upper())

    def set_icon(self, icon: str) -> None:
        """Update the icon.

        Args:
            icon: Icon name from IconProvider
        """
        self._icon = icon
        icon_text = IconProvider.get_icon(icon) if icon else ""
        if hasattr(self, "icon_label"):
            if icon_text:
                self.icon_label.setText(icon_text)
            else:
                self.icon_label.hide()

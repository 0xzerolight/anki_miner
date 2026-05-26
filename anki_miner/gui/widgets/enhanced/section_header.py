"""Section header widget for organizing UI sections."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class SectionHeader(QWidget):
    """Section header widget with optional action button.

    Features:
    - Large section title
    - Optional action button on the right
    - Divider line below
    - Clean, consistent styling

    Usage: Group related UI elements under descriptive headers
    """

    # Signal emitted when action button is clicked
    action_clicked = pyqtSignal()

    def __init__(self, title: str, action_text: str = "", parent=None):
        """Initialize the section header.

        Args:
            title: Section title text
            action_text: Optional action button text
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._title = title
        self._action_text = action_text

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, SPACING.xxs, SPACING.xs, SPACING.xxs)
        layout.setSpacing(SPACING.sm)

        self.title_label = QLabel(self._title)
        self.title_label.setObjectName("section-header")

        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h3)
        title_font.setWeight(QFont.Weight.Bold)
        self.title_label.setFont(title_font)

        layout.addWidget(self.title_label)
        layout.addStretch()

        # Optional action button
        if self._action_text:
            self.action_button = QPushButton(self._action_text)
            self.action_button.setObjectName("secondary")
            self.action_button.clicked.connect(self.action_clicked.emit)
            layout.addWidget(self.action_button)

        self.setLayout(layout)

        # Set size policy to allow growth when content needs more space
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(40)

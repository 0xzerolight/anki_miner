"""Section header widget for organizing UI sections."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class SectionHeader(QWidget):
    """Section header widget with optional icon and action button.

    Features:
    - Large section title
    - Optional icon
    - Optional action button on the right
    - Divider line below
    - Clean, consistent styling

    Usage: Group related UI elements under descriptive headers
    """

    # Signal emitted when action button is clicked
    action_clicked = pyqtSignal()

    def __init__(
        self, title: str, icon: str = "", action_text: str = "", action_icon: str = "", parent=None
    ):
        """Initialize the section header.

        Args:
            title: Section title text
            icon: Optional icon name from IconProvider
            action_text: Optional action button text
            action_icon: Optional action button icon
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._title = title
        self._icon = icon
        self._action_text = action_text
        self._action_icon = action_icon

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, SPACING.xxs, 0, SPACING.xxs)
        layout.setSpacing(SPACING.sm)

        # Title with optional icon
        title_text = self._title
        if self._icon:
            icon_char = IconProvider.get_icon(self._icon)
            title_text = f"{icon_char} {title_text}"

        self.title_label = QLabel(title_text)
        self.title_label.setObjectName("section-header")

        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h3)
        title_font.setWeight(QFont.Weight.Bold)
        self.title_label.setFont(title_font)

        layout.addWidget(self.title_label)
        layout.addStretch()

        # Optional action button
        if self._action_text or self._action_icon:
            action_text = self._action_text

            if self._action_icon:
                icon_char = IconProvider.get_icon(self._action_icon)
                action_text = f"{icon_char} {action_text}" if action_text else icon_char

            self.action_button = QPushButton(action_text)
            self.action_button.setObjectName("secondary")
            self.action_button.clicked.connect(self.action_clicked.emit)
            layout.addWidget(self.action_button)

        self.setLayout(layout)

        # Set size policy to allow growth when content needs more space
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(40)

    def set_title(self, title: str) -> None:
        """Update the section title.

        Args:
            title: New title text
        """
        self._title = title

        title_text = title
        if self._icon:
            icon_char = IconProvider.get_icon(self._icon)
            title_text = f"{icon_char} {title_text}"

        self.title_label.setText(title_text)

    def set_icon(self, icon: str) -> None:
        """Update the section icon.

        Args:
            icon: Icon name from IconProvider
        """
        self._icon = icon
        self.set_title(self._title)  # Refresh title with new icon

    def set_action_enabled(self, enabled: bool) -> None:
        """Enable or disable the action button.

        Args:
            enabled: Whether button should be enabled
        """
        if hasattr(self, "action_button"):
            self.action_button.setEnabled(enabled)

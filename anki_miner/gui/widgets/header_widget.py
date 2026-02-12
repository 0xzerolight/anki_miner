"""Header widget for main window.

Provides app branding, theme selection, and quick status indicators.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.resources.styles.theme import Theme


class HeaderWidget(QWidget):
    """Header widget with app branding and theme selection.

    Displays:
    - App title and subtitle
    - Theme selector
    - Quick status indicators
    """

    # Signal emitted when theme is changed
    theme_changed = pyqtSignal(str)  # theme name

    def __init__(self, parent=None):
        """Initialize the header widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout()
        layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)

        # Left side: App branding
        branding_layout = QVBoxLayout()
        branding_layout.setSpacing(2)  # Tight spacing for title/subtitle

        # App title
        title_label = QLabel("Anki Miner")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h2)
        title_font.setWeight(QFont.Weight.Bold)
        title_label.setFont(title_font)
        title_label.setObjectName("heading2")
        branding_layout.addWidget(title_label)

        # Subtitle
        subtitle_label = QLabel("Turn Immersion Into Vocabulary")
        subtitle_label.setObjectName("caption")
        subtitle_font = QFont()
        subtitle_font.setPixelSize(FONT_SIZES.caption)
        subtitle_label.setFont(subtitle_font)
        branding_layout.addWidget(subtitle_label)

        layout.addLayout(branding_layout)
        layout.addStretch()

        # Right side: Theme selector
        theme_layout = QHBoxLayout()
        theme_layout.setSpacing(SPACING.xs)

        theme_label = QLabel("Theme:")
        theme_label.setObjectName("caption")
        theme_layout.addWidget(theme_label)

        # Theme combo box
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(f"{IconProvider.get_icon('sun')} Light", "light")
        self.theme_combo.addItem(f"{IconProvider.get_icon('moon')} Dark", "dark")
        self.theme_combo.addItem(f"{IconProvider.get_icon('sakura')} Sakura", "sakura")

        # Set current theme
        current_theme = Theme.get_current_mode()
        theme_index = {"light": 0, "dark": 1, "sakura": 2}.get(current_theme, 0)
        self.theme_combo.setCurrentIndex(theme_index)

        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.theme_combo.setToolTip(
            "Select application theme: Light, Dark, or Sakura (Ctrl+T to cycle)"
        )
        theme_layout.addWidget(self.theme_combo)

        layout.addLayout(theme_layout)

        self.setLayout(layout)

        # Set object name for styling
        self.setObjectName("header-widget")

        # Alias for compatibility
        self.theme_selector = self.theme_combo

        # Apply custom styling
        self.setStyleSheet("""
            QWidget#header-widget {
                background-color: palette(window);
                border-bottom: 1px solid palette(mid);
            }
        """)

    def _on_theme_changed(self, index: int) -> None:
        """Handle theme selection change.

        Args:
            index: Selected combo box index
        """
        theme_name = self.theme_combo.itemData(index)
        if theme_name:
            # Update theme
            Theme.set_mode(theme_name)

            # Emit signal
            self.theme_changed.emit(theme_name)

    def update_theme_selector(self) -> None:
        """Update theme selector to match current theme."""
        current_theme = Theme.get_current_mode()
        theme_index = {"light": 0, "dark": 1, "sakura": 2}.get(current_theme, 0)

        # Block signals to avoid triggering theme change
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(theme_index)
        self.theme_combo.blockSignals(False)

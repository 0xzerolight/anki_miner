"""Header widget for main window.

Provides app branding, theme selection, and quick status indicators.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.resources.styles.theme import Theme

# Sentinel item data marking the "All themes…" entry that opens the Themes
# settings tab instead of switching themes. Picked to be distinct from any
# real theme key (which are filename stems — no leading underscore).
ALL_THEMES_SENTINEL = "__open_theme_settings__"


class HeaderWidget(QWidget):
    """Header widget with app branding and theme selection.

    The theme selector shows only the user's favorited themes plus an
    "All themes…" sentinel that opens the Themes tab in Settings. This keeps
    the top-right rotation focused even when many themes are installed.
    """

    # Active theme changed via this widget (theme key emitted).
    theme_changed = pyqtSignal(str)
    # User picked the "All themes…" sentinel — open the Themes settings tab.
    open_theme_settings = pyqtSignal()

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

        self.theme_combo = QComboBox()
        self._populate_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_layout.addWidget(self.theme_combo)

        layout.addLayout(theme_layout)

        self.setLayout(layout)
        self.setObjectName("header-widget")

    def _populate_theme_combo(self) -> None:
        """Rebuild the combo from current favorites + active theme + sentinel.

        Signals are blocked during the rebuild so callers can refresh after a
        favorites change without re-triggering theme apply.
        """
        self.theme_combo.blockSignals(True)
        try:
            self.theme_combo.clear()

            favorites = Theme.get_favorited_themes()
            current_mode = Theme.get_current_mode()
            available = Theme.get_available_themes()

            # If the active theme isn't in favorites (e.g. user unstarred it),
            # show it at the top so the dropdown still reflects reality and
            # the user isn't suddenly "missing" a theme they're actively using.
            if current_mode and current_mode not in favorites:
                display = available.get(current_mode, current_mode)
                self.theme_combo.addItem(display, current_mode)

            for key, display in favorites.items():
                self.theme_combo.addItem(display, key)

            # Sentinel entry that opens Settings → Themes.
            self.theme_combo.addItem("All themes…", ALL_THEMES_SENTINEL)

            # Select active theme.
            for i in range(self.theme_combo.count()):
                if self.theme_combo.itemData(i) == current_mode:
                    self.theme_combo.setCurrentIndex(i)
                    break

            tooltip_names = ", ".join(available.values())
            self.theme_combo.setToolTip(
                "Active theme. Top-right shows favorites; pick 'All themes…' to manage them. "
                f"(Ctrl+T cycles favorites). Installed: {tooltip_names}"
            )
        finally:
            self.theme_combo.blockSignals(False)

    def _on_theme_changed(self, index: int) -> None:
        """Handle theme selection change.

        Args:
            index: Selected combo box index
        """
        data = self.theme_combo.itemData(index)
        if data == ALL_THEMES_SENTINEL:
            # Snap selection back to the active theme so the sentinel never
            # appears "selected" in the closed combo.
            self.update_theme_selector()
            self.open_theme_settings.emit()
            return
        if data:
            Theme.set_mode(data)
            self.theme_changed.emit(data)

    def update_theme_selector(self) -> None:
        """Update theme selector to match current theme without re-emitting."""
        current_theme = Theme.get_current_mode()
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == current_theme:
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(i)
                self.theme_combo.blockSignals(False)
                return

        # Active theme not in combo (favorites changed and dropped it).
        # Rebuild so the active theme reappears at the top.
        self.refresh_favorites()

    def refresh_favorites(self) -> None:
        """Rebuild the combo after favorites have changed.

        Call this from MainWindow whenever the Themes settings panel mutates
        the favorites list, so the top-right selector stays in sync.
        """
        self._populate_theme_combo()

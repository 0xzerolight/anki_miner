"""Enhanced dialog base class for consistent dialog styling."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class EnhancedDialog(QDialog):
    """Base dialog with standard header, content area, and button footer.

    Provides:
    - Consistent header with large icon and title
    - Optional subtitle
    - Content area for custom widgets
    - Standard button footer with consistent styling
    - Escape key handling

    Usage:
        dialog = EnhancedDialog(parent)
        dialog.set_header("success", "Processing Complete", "3 cards created")
        dialog.add_content(results_widget)
        dialog.add_button("Close", "primary")
        dialog.exec()
    """

    def __init__(self, parent=None, title: str = ""):
        """Initialize the dialog.

        Args:
            parent: Parent widget
            title: Window title
        """
        super().__init__(parent)

        if title:
            self.setWindowTitle(title)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setMinimumWidth(400)

        # Main layout
        self._main_layout = QVBoxLayout()
        self._main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)
        self._main_layout.setSpacing(SPACING.lg)

        # Header area (icon + title + subtitle)
        self._header_widget = QWidget()
        self._header_layout = QHBoxLayout()
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setSpacing(SPACING.md)

        # Icon label (large)
        self._icon_label = QLabel()
        icon_font = QFont()
        icon_font.setPixelSize(FONT_SIZES.icon_large)
        self._icon_label.setFont(icon_font)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.hide()  # Hidden until set_header is called

        # Title container
        title_container = QWidget()
        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(SPACING.xxs)

        self._title_label = QLabel()
        self._title_label.setObjectName("heading2")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h2)
        title_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(title_font)
        self._title_label.hide()

        self._subtitle_label = QLabel()
        self._subtitle_label.setObjectName("caption")
        subtitle_font = QFont()
        subtitle_font.setPixelSize(FONT_SIZES.body)
        self._subtitle_label.setFont(subtitle_font)
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.hide()

        title_layout.addWidget(self._title_label)
        title_layout.addWidget(self._subtitle_label)
        title_container.setLayout(title_layout)

        self._header_layout.addWidget(self._icon_label)
        self._header_layout.addWidget(title_container, 1)
        self._header_widget.setLayout(self._header_layout)
        self._header_widget.hide()

        self._main_layout.addWidget(self._header_widget)

        # Content area
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(SPACING.md)
        self._content_widget.setLayout(self._content_layout)

        self._main_layout.addWidget(self._content_widget, 1)

        # Button footer
        self._footer_widget = QWidget()
        self._footer_layout = QHBoxLayout()
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_layout.setSpacing(SPACING.sm)
        self._footer_layout.addStretch()
        self._footer_widget.setLayout(self._footer_layout)
        self._footer_widget.hide()

        self._main_layout.addWidget(self._footer_widget)

        self.setLayout(self._main_layout)

    def set_header(self, icon: str, title: str, subtitle: str = "") -> None:
        """Set the dialog header.

        Args:
            icon: Icon name from IconProvider
            title: Main title text
            subtitle: Optional subtitle/description
        """
        # Set icon
        icon_char = IconProvider.get_icon(icon)
        self._icon_label.setText(icon_char)
        self._icon_label.show()

        # Set title
        self._title_label.setText(title)
        self._title_label.show()

        # Set subtitle if provided
        if subtitle:
            self._subtitle_label.setText(subtitle)
            self._subtitle_label.show()
        else:
            self._subtitle_label.hide()

        self._header_widget.show()

    def add_content(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """Add a widget to the content area.

        Args:
            widget: Widget to add
            stretch: Layout stretch factor

        Returns:
            The widget that was added
        """
        self._content_layout.addWidget(widget, stretch)
        return widget

    def add_content_layout(self, layout) -> None:
        """Add a layout to the content area.

        Args:
            layout: Layout to add
        """
        self._content_layout.addLayout(layout)

    def add_button(self, text: str, variant: str = "secondary", callback=None) -> QPushButton:
        """Add a button to the footer.

        Args:
            text: Button text
            variant: Button variant (primary, secondary, ghost, danger)
            callback: Optional click callback

        Returns:
            The created button
        """
        button = QPushButton(text)
        button.setObjectName(variant)

        if callback:
            button.clicked.connect(callback)

        self._footer_layout.addWidget(button)
        self._footer_widget.show()

        return button

    def add_close_button(self, text: str = "Close") -> QPushButton:
        """Add a close button that closes the dialog.

        Args:
            text: Button text

        Returns:
            The created button
        """
        return self.add_button(text, "primary", self.accept)

    def add_cancel_button(self, text: str = "Cancel") -> QPushButton:
        """Add a cancel button that rejects the dialog.

        Args:
            text: Button text

        Returns:
            The created button
        """
        return self.add_button(text, "secondary", self.reject)

    def set_content_spacing(self, spacing: int) -> None:
        """Set spacing between content items.

        Args:
            spacing: Spacing in pixels
        """
        self._content_layout.setSpacing(spacing)

    def keyPressEvent(self, event) -> None:
        """Handle key press events."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    @property
    def content_layout(self) -> QVBoxLayout:
        """Get the content layout for direct manipulation."""
        return self._content_layout

    @property
    def footer_layout(self) -> QHBoxLayout:
        """Get the footer layout for direct manipulation."""
        return self._footer_layout

"""Enhanced log widget for displaying messages with color coding and controls."""

from datetime import datetime

from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.constants import LOG_MAX_LINES, LOG_ROTATION_THRESHOLD, MIN_HEIGHT_LOG_WIDGET
from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class LogWidget(QWidget):
    """Enhanced log widget with controls and card styling.

    Features:
    - Color-coded messages (info, success, warning, error)
    - Header bar with Clear and Copy buttons
    - Timestamps for each message
    - Auto-scroll with user override
    - Log rotation to prevent memory issues (max 1000 lines)
    - Card-style container with rounded corners
    """

    MAX_LINES = LOG_MAX_LINES

    def __init__(self, parent=None):
        """Initialize the log widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._auto_scroll = True
        self._line_count = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Set minimum height to prevent collapsing
        self.setMinimumHeight(MIN_HEIGHT_LOG_WIDGET)

        # Header bar
        header = QWidget()
        header.setObjectName("log-header")
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)

        # Title
        title_label = QLabel("Activity Log")
        title_font = QFont()
        title_font.setWeight(QFont.Weight.Bold)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # Copy button
        self.copy_button = QPushButton(f"{IconProvider.get_icon('export')} Copy")
        self.copy_button.setObjectName("ghost")
        self.copy_button.clicked.connect(self._on_copy_clicked)
        self.copy_button.setToolTip("Copy all log content to clipboard")
        header_layout.addWidget(self.copy_button)

        # Clear button
        self.clear_button = QPushButton(f"{IconProvider.get_icon('delete')} Clear")
        self.clear_button.setObjectName("ghost")
        self.clear_button.clicked.connect(self._on_clear_clicked)
        self.clear_button.setToolTip("Clear all log messages")
        header_layout.addWidget(self.clear_button)

        header.setLayout(header_layout)
        layout.addWidget(header)

        # Text edit for log content
        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("log-widget")
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        # Set monospace font for better readability
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(FONT_SIZES.body_sm)
        self.text_edit.setFont(font)

        layout.addWidget(self.text_edit)

        self.setLayout(layout)

        # Apply card styling
        self.setObjectName("card")

        # Set size policy to allow expansion
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def append_info(self, message: str) -> None:
        """Append an info message.

        Args:
            message: Message to append
        """
        icon = IconProvider.get_icon("info")
        formatted_message = f"{icon} {message}"
        self._append_message(formatted_message, "info")

    def append_success(self, message: str) -> None:
        """Append a success message in green.

        Args:
            message: Message to append
        """
        icon = IconProvider.get_icon("success")
        formatted_message = f"{icon} {message}"
        self._append_message(formatted_message, "success")

    def append_warning(self, message: str) -> None:
        """Append a warning message in orange.

        Args:
            message: Message to append
        """
        icon = IconProvider.get_icon("warning")
        formatted_message = f"{icon} {message}"
        self._append_message(formatted_message, "warning")

    def append_error(self, message: str) -> None:
        """Append an error message in red.

        Args:
            message: Message to append
        """
        icon = IconProvider.get_icon("error")
        formatted_message = f"{icon} {message}"
        self._append_message(formatted_message, "error")

    def _append_message(self, text: str, level: str) -> None:
        """Append a message with timestamp and color.

        Args:
            text: Message text
            level: Message level ('info', 'success', 'warning', 'error')
        """
        # Get timestamp
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Check for log rotation
        if self._line_count >= self.MAX_LINES:
            self._rotate_log()

        # Get cursor
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Insert timestamp in gray
        timestamp_format = cursor.charFormat()
        timestamp_format.setForeground(QColor("#6B7280"))  # Gray
        cursor.setCharFormat(timestamp_format)
        cursor.insertText(f"[{timestamp}] ")

        # Insert message with appropriate color
        message_format = cursor.charFormat()

        if level == "success":
            message_format.setForeground(QColor("#10B981"))  # Green
        elif level == "warning":
            message_format.setForeground(QColor("#F59E0B"))  # Orange
        elif level == "error":
            message_format.setForeground(QColor("#EF4444"))  # Red
        else:  # info
            message_format.setForeground(QColor("#3B82F6"))  # Blue

        cursor.setCharFormat(message_format)
        cursor.insertText(text + "\n")

        self._line_count += 1

        # Auto-scroll if enabled
        if self._auto_scroll:
            self.text_edit.setTextCursor(cursor)
            self.text_edit.ensureCursorVisible()

    def _rotate_log(self) -> None:
        """Remove oldest log entries to prevent memory issues.

        Uses QTextDocument block operations to preserve color formatting.
        """
        doc = self.text_edit.document()
        if doc is None:
            return
        block_count = doc.blockCount()

        if block_count > LOG_ROTATION_THRESHOLD:
            # Remove oldest blocks to keep only LOG_ROTATION_THRESHOLD
            blocks_to_remove = block_count - LOG_ROTATION_THRESHOLD
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(blocks_to_remove):
                cursor.movePosition(
                    QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor
                )
            cursor.removeSelectedText()
            cursor.deleteChar()  # Remove trailing newline
            self._line_count = LOG_ROTATION_THRESHOLD

    def _on_copy_clicked(self) -> None:
        """Handle copy button click."""
        # Copy all log content to clipboard
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self.text_edit.toPlainText())

        # Provide feedback (could show a temporary "Copied!" message)
        self.copy_button.setText(f"{IconProvider.get_icon('success')} Copied!")

        # Reset button text after a delay
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(
            2000, lambda: self.copy_button.setText(f"{IconProvider.get_icon('export')} Copy")
        )

    def _on_clear_clicked(self) -> None:
        """Handle clear button click."""
        self.clear_log()

    def clear_log(self) -> None:
        """Clear all log messages."""
        self.text_edit.clear()
        self._line_count = 0

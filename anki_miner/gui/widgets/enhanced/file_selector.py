"""File selector widget with integrated browse button and validation."""

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import make_label_fit_text


class FileSelector(QWidget):
    """Enhanced file selector with validation and drag-drop support.

    Features:
    - Integrated label, input, and browse button
    - File validation with visual indicators
    - Drag-and-drop support
    - Shows current file/folder name below input
    - Emits signals when path changes or is validated

    Signals:
        path_changed: Emitted when path changes (str: new_path)
        path_validated: Emitted when path is validated (bool: is_valid, str: path)
    """

    path_changed = pyqtSignal(str)  # new path
    path_validated = pyqtSignal(bool, str)  # is_valid, path

    def __init__(
        self,
        label: str = "File:",
        file_mode: bool = True,
        file_filter: str = "All Files (*)",
        placeholder: str = "",
        parent=None,
    ):
        """Initialize the file selector.

        Args:
            label: Label text
            file_mode: True for file selection, False for folder selection
            file_filter: File filter for dialog (only used if file_mode=True)
            placeholder: Placeholder text for input field
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._file_mode = file_mode
        self._file_filter = file_filter
        self._placeholder = placeholder
        self._is_valid = False

        self._label_text = label
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        # Main row: Label + Input + Browse button
        main_layout = QHBoxLayout()
        main_layout.setSpacing(SPACING.xs)

        # Label - only add if label text is not empty
        self.label: QLabel | None = None
        if self._label_text:
            self.label = QLabel(self._label_text)
            self.label.setObjectName("field-label")
            self.label.setMinimumWidth(100)
            make_label_fit_text(self.label)
            main_layout.addWidget(self.label)

        # Input field
        self.input = QLineEdit()

        # Set placeholder text
        if self._placeholder:
            placeholder = self._placeholder
        elif self._file_mode:
            placeholder = "Select file..."
        else:
            placeholder = "Select folder..."

        self.input.setPlaceholderText(placeholder)
        self.input.textChanged.connect(self._on_text_changed)
        main_layout.addWidget(self.input)

        # Browse button
        self.browse_button = QPushButton(f"{IconProvider.get_icon('browse')} Browse...")
        self.browse_button.clicked.connect(self._on_browse_clicked)
        main_layout.addWidget(self.browse_button)

        layout.addLayout(main_layout)

        # Status label (shows current file/folder name or validation message)
        self.status_label = QLabel("")
        self.status_label.setObjectName("caption")
        self.status_label.setWordWrap(True)  # Allow wrapping for long paths
        status_font = QFont()
        status_font.setPixelSize(FONT_SIZES.caption)
        self.status_label.setFont(status_font)
        make_label_fit_text(self.status_label)  # Background fits text width
        layout.addWidget(self.status_label)

        self.setLayout(layout)

        # Set size policy to prevent compression
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        # Enable drag and drop
        self.setAcceptDrops(True)

        # Set accessibility properties
        file_or_folder = "file" if self._file_mode else "folder"
        self.setAccessibleName(self._label_text)
        self.setAccessibleDescription(
            f"Select a {file_or_folder} by typing path, browsing, or dragging"
        )

        self.input.setAccessibleName(f"{self._label_text} path")
        self.input.setAccessibleDescription(
            f"Path to {file_or_folder}. Type or paste a path, or use browse button"
        )

        self.browse_button.setAccessibleName(f"Browse for {file_or_folder}")
        self.browse_button.setAccessibleDescription(f"Opens file dialog to select {file_or_folder}")

        # Set proper tab order
        self.setTabOrder(self.input, self.browse_button)

        # Initial status
        self._update_status()

    def _on_text_changed(self, text: str) -> None:
        """Handle input text change.

        Args:
            text: New text value
        """
        self._validate_path(text)
        self.path_changed.emit(text)

    def browse(self) -> None:
        """Open file/folder browser dialog.

        This is a public method that can be called from keyboard shortcuts.
        """
        self._on_browse_clicked()

    def _on_browse_clicked(self) -> None:
        """Handle browse button click."""
        if self._file_mode:
            # File selection
            file_path, _ = QFileDialog.getOpenFileName(
                self, f"Select {self._label_text}", "", self._file_filter
            )
            if file_path:
                self.input.setText(file_path)
        else:
            # Folder selection
            folder_path = QFileDialog.getExistingDirectory(self, f"Select {self._label_text}", "")
            if folder_path:
                self.input.setText(folder_path)

    def _validate_path(self, path_str: str) -> None:
        """Validate the provided path.

        Args:
            path_str: Path string to validate
        """
        if not path_str:
            self._is_valid = False
            self.input.setProperty("error", False)
            self.input.setProperty("success", False)
            self._update_status()
            self.path_validated.emit(False, "")
            return

        path = Path(path_str)

        is_valid = path.is_file() if self._file_mode else path.is_dir()

        self._is_valid = is_valid

        # Update input styling
        self.input.setProperty("error", not is_valid)
        self.input.setProperty("success", is_valid)

        # Force style refresh
        if style := self.input.style():
            style.unpolish(self.input)
            style.polish(self.input)

        self._update_status()
        self.path_validated.emit(is_valid, path_str)

    def _update_status(self) -> None:
        """Update the status label."""
        path_str = self.input.text()

        if not path_str:
            # No file/folder selected
            if self._file_mode:
                icon = IconProvider.get_icon("video_file")
                self.status_label.setText(f"{icon} No file selected")
            else:
                icon = IconProvider.get_icon("folder")
                self.status_label.setText(f"{icon} No folder selected")
            return

        path = Path(path_str)

        if self._is_valid:
            # Show file/folder name with success icon
            icon = IconProvider.get_icon("success")
            name = path.name
            self.status_label.setText(f"{icon} {name}")
        else:
            # Show error message
            icon = IconProvider.get_icon("error")
            if self._file_mode:
                self.status_label.setText(f"{icon} File not found")
            else:
                self.status_label.setText(f"{icon} Folder not found")

    def get_path(self) -> str:
        """Get the current path.

        Returns:
            Current path string
        """
        return self.input.text()

    def set_path(self, path: str) -> None:
        """Set the path.

        Args:
            path: Path to set
        """
        self.input.setText(path)

    def is_valid(self) -> bool:
        """Check if current path is valid.

        Returns:
            True if path is valid, False otherwise
        """
        return self._is_valid

    def clear(self) -> None:
        """Clear the current path."""
        self.input.clear()

    # Drag and drop support
    def dragEnterEvent(self, event):
        """Handle drag enter event."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle drop event."""
        urls = event.mimeData().urls()
        if urls:
            # Get first file/folder
            file_path = urls[0].toLocalFile()
            self.input.setText(file_path)
            event.acceptProposedAction()

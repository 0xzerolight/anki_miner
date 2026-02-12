"""Unified status badge widget for consistent status indicators."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import QLabel, QSizePolicy

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES


class StatusBadge(QLabel):
    """Unified status badge with auto-refresh styling.

    Replaces multiple status indicator implementations with a single,
    consistent component. Features:
    - Pill-shaped badge with color-coded backgrounds
    - Icon + text display
    - Auto style refresh when status changes
    - Optional click handling

    Status types: checking, success, error, warning, info, pending

    QSS styling uses [status="value"] selectors:
        QLabel#status-badge[status="success"] { background: green; }
        QLabel#status-badge[status="error"] { background: red; }
    """

    clicked = pyqtSignal()

    # Standard status types
    STATUS_CHECKING = "checking"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_WARNING = "warning"
    STATUS_INFO = "info"
    STATUS_PENDING = "pending"

    def __init__(self, name: str, status: str = "checking", clickable: bool = True, parent=None):
        """Initialize the status badge.

        Args:
            name: Display name (e.g., "AnkiConnect", "ffmpeg")
            status: Initial status type
            clickable: Whether badge responds to clicks
            parent: Parent widget
        """
        super().__init__(parent)
        self._name = name
        self._status = status
        self._clickable = clickable

        self._setup_ui()
        self._update_display()

    def _setup_ui(self) -> None:
        """Set up the badge UI."""
        self.setObjectName("status-badge")

        # Configure font
        font = QFont()
        font.setPixelSize(FONT_SIZES.caption)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)

        # Auto-size to content
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        # Clickable cursor
        if self._clickable:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.setMouseTracking(True)

    def _update_display(self) -> None:
        """Update badge text and styling."""
        icon_map = {
            self.STATUS_CHECKING: "progress",
            self.STATUS_SUCCESS: "success",
            self.STATUS_ERROR: "error",
            self.STATUS_WARNING: "warning",
            self.STATUS_INFO: "info",
            self.STATUS_PENDING: "pending",
        }

        icon_name = icon_map.get(self._status, "progress")
        icon = IconProvider.get_icon(icon_name)
        self.setText(f"{icon} {self._name}")

        # Set property for QSS styling and refresh
        self.setProperty("status", self._status)
        if style := self.style():
            style.unpolish(self)
            style.polish(self)

    def set_status(self, status: str, tooltip: str = "") -> None:
        """Update the badge status.

        Args:
            status: Status type (checking, success, error, warning, info, pending)
            tooltip: Optional tooltip text
        """
        self._status = status
        self._update_display()

        if tooltip:
            self.setToolTip(tooltip)

    def set_name(self, name: str) -> None:
        """Update the display name.

        Args:
            name: New display name
        """
        self._name = name
        self._update_display()

    @property
    def status(self) -> str:
        """Get current status."""
        return self._status

    @property
    def name(self) -> str:
        """Get display name."""
        return self._name

    def mousePressEvent(self, event) -> None:
        """Handle mouse press event."""
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

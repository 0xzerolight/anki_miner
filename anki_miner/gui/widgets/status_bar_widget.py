"""Enhanced status bar widget with sections and rich display."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStatusBar, QWidget

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import StatusBadge


class StatusBarWidget(QStatusBar):
    """Enhanced status bar with three sections.

    Uses the unified StatusBadge component for system status indicators.

    Features:
    - Left section: Current operation status with icon
    - Center section: Session statistics
    - Right section: System status indicators (AnkiConnect, ffmpeg)
    - Clickable system status for detailed validation

    Signals:
        system_status_clicked: Emitted when system status is clicked
    """

    system_status_clicked = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the status bar widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._cards_created_session = 0
        self._ankiconnect_status = False
        self._ffmpeg_status = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setObjectName("status-bar")
        self.setContentsMargins(SPACING.sm, 6, SPACING.sm, 6)

        # Left section: Current operation
        self.operation_label = QLabel(f"{IconProvider.get_icon('info')} Ready")
        self.operation_label.setObjectName("status-operation")
        operation_font = QFont()
        operation_font.setWeight(QFont.Weight.Medium)
        self.operation_label.setFont(operation_font)
        self.addWidget(self.operation_label, 1)  # Stretch factor 1

        # Separator 1
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.VLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        separator1.setObjectName("status-separator")
        self.addWidget(separator1)

        # Center section: Statistics
        self.stats_label = QLabel(f"{IconProvider.get_icon('card')} 0 cards this session")
        self.stats_label.setObjectName("status-stats")
        stats_font = QFont()
        stats_font.setPixelSize(FONT_SIZES.caption)
        self.stats_label.setFont(stats_font)
        self.addWidget(self.stats_label)

        # Separator 2
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.VLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        separator2.setObjectName("status-separator")
        self.addPermanentWidget(separator2)

        # Right section: System status (clickable container)
        self.system_status_widget = QWidget()
        self.system_status_widget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.system_status_widget.setToolTip("Click to view detailed system validation")
        self.system_status_widget.mousePressEvent = lambda event: self._on_system_status_clicked(event)  # type: ignore[method-assign,assignment]

        system_layout = QHBoxLayout()
        system_layout.setContentsMargins(0, 0, 0, 0)
        system_layout.setSpacing(SPACING.sm)

        # Use StatusBadge for consistent status indicators
        self.anki_status_badge = StatusBadge("AnkiConnect", status="checking", clickable=False)
        self.anki_status_badge.setObjectName("status-indicator")  # Keep existing QSS selector
        system_layout.addWidget(self.anki_status_badge)

        self.ffmpeg_status_badge = StatusBadge("ffmpeg", status="checking", clickable=False)
        self.ffmpeg_status_badge.setObjectName("status-indicator")  # Keep existing QSS selector
        system_layout.addWidget(self.ffmpeg_status_badge)

        self.system_status_widget.setLayout(system_layout)
        self.addPermanentWidget(self.system_status_widget)

        # Initial status update
        self._update_system_status()

    def set_operation(self, message: str, level: str = "info") -> None:
        """Set the current operation message.

        Args:
            message: Operation message
            level: Message level ('info', 'success', 'warning', 'error')
        """
        icon_map = {"info": "info", "success": "success", "warning": "warning", "error": "error"}

        icon = IconProvider.get_icon(icon_map.get(level, "info"))
        self.operation_label.setText(f"{icon} {message}")

        # Set appropriate property for styling and refresh
        self.operation_label.setProperty("level", level)
        if style := self.operation_label.style():
            style.unpolish(self.operation_label)
            style.polish(self.operation_label)

    def increment_cards_created(self, count: int = 1) -> None:
        """Increment the session card counter.

        Args:
            count: Number of cards to add (default: 1)
        """
        self._cards_created_session += count
        self._update_stats()

    def reset_session_stats(self) -> None:
        """Reset session statistics."""
        self._cards_created_session = 0
        self._update_stats()

    def set_system_status(self, ankiconnect: bool, ffmpeg: bool) -> None:
        """Update system status indicators.

        Args:
            ankiconnect: Whether AnkiConnect is available
            ffmpeg: Whether ffmpeg is available
        """
        self._ankiconnect_status = ankiconnect
        self._ffmpeg_status = ffmpeg
        self._update_system_status()

    def _update_stats(self) -> None:
        """Update the statistics display."""
        card_icon = IconProvider.get_icon("card")

        if self._cards_created_session == 1:
            text = f"{card_icon} 1 card this session"
        else:
            text = f"{card_icon} {self._cards_created_session} cards this session"

        self.stats_label.setText(text)

    def _update_system_status(self) -> None:
        """Update the system status indicators using StatusBadge."""
        # AnkiConnect status
        status = "success" if self._ankiconnect_status else "error"
        tooltip = (
            "AnkiConnect is connected"
            if self._ankiconnect_status
            else "AnkiConnect is not connected"
        )
        self.anki_status_badge.set_status(status, tooltip)

        # ffmpeg status
        status = "success" if self._ffmpeg_status else "error"
        tooltip = "ffmpeg is available" if self._ffmpeg_status else "ffmpeg is not available"
        self.ffmpeg_status_badge.set_status(status, tooltip)

    def _on_system_status_clicked(self, event) -> None:
        """Handle system status click.

        Args:
            event: Mouse event
        """
        self.system_status_clicked.emit()

    # Backward compatibility properties
    @property
    def anki_status_label(self):
        """Get anki status badge (backward compatibility)."""
        return self.anki_status_badge

    @property
    def ffmpeg_status_label(self):
        """Get ffmpeg status badge (backward compatibility)."""
        return self.ffmpeg_status_badge

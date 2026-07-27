"""Enhanced status bar widget with sections and rich display."""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStatusBar, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import StatusBadge

#: How long a transient operation message stays before reverting to the idle
#: text. Errors are exempt: an unresolved problem is exactly what must not
#: quietly disappear.
OPERATION_EXPIRY_MS = 8000


def _health_presentation(state: bool | None, *, unknown: str, ok: str, failed: str) -> tuple[str, str]:
    """Map a tri-state dependency health value to (badge status, tooltip).

    ``None`` means "not probed yet" and must render as *checking*, never as an
    error. Painting unknown as failure made a healthy app announce two broken
    dependencies on every launch, before a single probe had run.
    """
    if state is None:
        return "checking", unknown
    return ("success", ok) if state else ("error", failed)


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
        # Tri-state: None until a probe has actually reported.
        self._ankiconnect_status: bool | None = None
        self._ffmpeg_status: bool | None = None
        self._operation_timer = QTimer(self)
        self._operation_timer.setSingleShot(True)
        self._operation_timer.setInterval(OPERATION_EXPIRY_MS)
        self._operation_timer.timeout.connect(self.clear_operation)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setObjectName("status-bar")
        self.setContentsMargins(SPACING.sm, 6, SPACING.sm, 6)

        # Left section: Current operation
        self.operation_label = QLabel(self.tr("Ready"))
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
        self.stats_label = QLabel(self.tr("%n card(s) this session", "", self._cards_created_session))
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
        self.system_status_widget.setToolTip(self.tr("Click to view detailed system validation"))
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
        self._operation_timer.stop()
        self._render_operation(message, level)

        # Errors stay put; everything else is a transient note about a moment
        # that has passed, and must not outlive it.
        if level != "error":
            self._operation_timer.start()

    def clear_operation(self) -> None:
        """Revert to the idle message. Safe to call repeatedly."""
        self._operation_timer.stop()
        # Literal, not a constant: Qt extracts translatable strings
        # statically, so tr(SOME_CONST) yields no catalog entry.
        self._render_operation(self.tr("Ready"), "info")

    def _render_operation(self, message: str, level: str) -> None:
        """Paint the operation text and restyle it for its level."""
        self.operation_label.setText(message)
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

    def set_system_status(self, ankiconnect: bool, ffmpeg: bool) -> None:
        """Update system status indicators.

        Args:
            ankiconnect: Whether AnkiConnect is available
            ffmpeg: Whether ffmpeg is available
        """
        self._ankiconnect_status = ankiconnect
        self._ffmpeg_status = ffmpeg
        self._update_system_status()

    def set_system_status_checking(self) -> None:
        """Return both indicators to the not-yet-known state.

        Used when a re-probe starts: a check in flight is not a failure.
        """
        self._ankiconnect_status = None
        self._ffmpeg_status = None
        self._update_system_status()

    def _update_stats(self) -> None:
        """Update the statistics display."""
        self.stats_label.setText(self.tr("%n card(s) this session", "", self._cards_created_session))

    def _update_system_status(self) -> None:
        """Render both dependency badges from their tri-state values."""
        self.anki_status_badge.set_status(
            *_health_presentation(
                self._ankiconnect_status,
                unknown=self.tr("Checking AnkiConnect…"),
                ok=self.tr("AnkiConnect is connected"),
                failed=self.tr("AnkiConnect is not connected"),
            )
        )
        self.ffmpeg_status_badge.set_status(
            *_health_presentation(
                self._ffmpeg_status,
                unknown=self.tr("Checking ffmpeg…"),
                ok=self.tr("ffmpeg is available"),
                failed=self.tr("ffmpeg is not available"),
            )
        )

    def _on_system_status_clicked(self, event) -> None:
        """Handle system status click.

        Args:
            event: Mouse event
        """
        self.system_status_clicked.emit()

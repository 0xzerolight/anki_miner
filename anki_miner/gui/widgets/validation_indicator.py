"""Validation status indicator widget using unified StatusBadge."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.base import StatusBadge
from anki_miner.models import ValidationResult


class ValidationIndicator(QWidget):
    """Validation status indicator with badge styling.

    Uses the unified StatusBadge component for consistent status display.

    Features:
    - Pill-shaped status badges
    - Color-coded backgrounds (green, red, amber, gray)
    - Icon + text combination
    - Click handler to show detailed validation

    This widget shows the status of required system components:
    - AnkiConnect
    - ffmpeg
    - Deck
    - Note Type
    """

    validation_clicked = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the validation indicator."""
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        # Create status badges using unified StatusBadge component
        self.ankiconnect_badge = StatusBadge("AnkiConnect", status="checking")
        self.ankiconnect_badge.clicked.connect(self.validation_clicked.emit)

        self.ffmpeg_badge = StatusBadge("ffmpeg", status="checking")
        self.ffmpeg_badge.clicked.connect(self.validation_clicked.emit)

        self.deck_badge = StatusBadge("Deck", status="checking")
        self.deck_badge.clicked.connect(self.validation_clicked.emit)

        self.notetype_badge = StatusBadge("Note Type", status="checking")
        self.notetype_badge.clicked.connect(self.validation_clicked.emit)

        layout.addWidget(self.ankiconnect_badge)
        layout.addWidget(self.ffmpeg_badge)
        layout.addWidget(self.deck_badge)
        layout.addWidget(self.notetype_badge)
        layout.addStretch()

        self.setLayout(layout)

    def update_status(self, result: ValidationResult) -> None:
        """Update status indicators based on validation result.

        Args:
            result: Validation result
        """
        # Find specific issues for each component
        ankiconnect_issue = next(
            (issue for issue in result.issues if issue.component == "AnkiConnect"), None
        )
        ffmpeg_issue = next((issue for issue in result.issues if issue.component == "ffmpeg"), None)
        deck_issue = next(
            (issue for issue in result.issues if "deck" in issue.component.lower()), None
        )
        notetype_issue = next(
            (issue for issue in result.issues if "note" in issue.component.lower()), None
        )

        # Update badges
        self._update_badge(
            self.ankiconnect_badge,
            result.ankiconnect_ok,
            (
                ankiconnect_issue.message
                if ankiconnect_issue
                else "AnkiConnect is running and accessible"
            ),
        )

        self._update_badge(
            self.ffmpeg_badge,
            result.ffmpeg_ok,
            ffmpeg_issue.message if ffmpeg_issue else "ffmpeg is installed and accessible",
        )

        self._update_badge(
            self.deck_badge,
            result.deck_exists,
            deck_issue.message if deck_issue else "Anki deck exists",
        )

        self._update_badge(
            self.notetype_badge,
            result.note_type_exists,
            notetype_issue.message if notetype_issue else "Note type exists",
        )

    def _update_badge(self, badge: StatusBadge, is_ok: bool, tooltip: str) -> None:
        """Update a single validation badge.

        Args:
            badge: Badge to update
            is_ok: Whether the component is OK
            tooltip: Tooltip text
        """
        status = "success" if is_ok else "error"
        badge.set_status(status, tooltip)

    def set_checking(self) -> None:
        """Set all badges to checking state."""
        for badge in [
            self.ankiconnect_badge,
            self.ffmpeg_badge,
            self.deck_badge,
            self.notetype_badge,
        ]:
            badge.set_status("checking", "Validating system components...")

    def reset(self) -> None:
        """Reset all indicators to neutral state."""
        for badge in [
            self.ankiconnect_badge,
            self.ffmpeg_badge,
            self.deck_badge,
            self.notetype_badge,
        ]:
            badge.set_status("checking", "Click to validate")

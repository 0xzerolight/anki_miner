"""Unified status badge widget for consistent status indicators."""

# pyqtProperty is present at runtime but missing from the PyQt6 stubs.
from PyQt6.QtCore import Qt, pyqtProperty, pyqtSignal  # type: ignore[attr-defined]
from PyQt6.QtGui import QCursor, QFont, QHideEvent
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QSizePolicy

from anki_miner.gui.resources.styles import FONT_SIZES, MOTION
from anki_miner.gui.utils import motion

#: How far the pill dips on its way to the new state. Deliberately not zero: a
#: pill that disappears reads as the badge having been removed, and the row it
#: sits in visibly loses an element for a fifth of a second.
_FADE_FLOOR = 0.35


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

    A state change **fades out and back** rather than blinking into a different
    word (D36-B). The badge sits in the corner of the eye for the whole of a
    forty-minute run, and an instant swap there is caught as a flicker with no
    hint of what changed; the dip is what makes the eye come back and read it.
    The colour easing is Qt's stock ease-out, not the house spatial curve --
    D37 keeps the signature for things that travel, because a strongly
    flavoured curve on a tint reads as fussy.

    The word and the colour swap **at the dip**, not after it: motion is never
    on the critical path, so the badge is never left showing a status it has
    already stopped believing.
    """

    clicked = pyqtSignal()

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
        self._fade = 1.0

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
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)

        # The fade compositor. An effect rather than custom painting, so the
        # pill keeps being drawn by the stylesheet: 29 bundled themes author
        # these colours, and a hand-painted badge would answer to none of them.
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        # Clickable cursor
        if self._clickable:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Fade
    # ------------------------------------------------------------------

    def _get_fade_progress(self) -> float:
        return self._fade

    def _set_fade_progress(self, value: float) -> None:
        self._fade = value
        self._opacity_effect.setOpacity(value)

    fadeProgress = pyqtProperty(float, fget=_get_fade_progress, fset=_set_fade_progress)  # noqa: N815 - Qt property

    def _fade_through(self) -> None:
        """Dip the pill, then let it come back up.

        The dip is applied synchronously and repainted before the animation
        starts: if the GUI thread is about to block on whatever produced the
        new status, the user still sees the pill acknowledge it. That is the
        same reason ``ModernButton`` tints under the finger before the work
        begins.
        """
        self._set_fade_progress(_FADE_FLOOR)
        self.repaint()
        motion.animate(self, b"fadeProgress", 1.0, duration=MOTION.state, curve=motion.colour_curve())

    def hideEvent(self, event: QHideEvent | None) -> None:  # noqa: N802 - Qt override
        """Drop the fade outright: nobody is watching a hidden badge settle.

        Without this, a badge hidden mid-dip comes back at 35% and stays there
        until its next state change.
        """
        super().hideEvent(event)
        for animation in motion.active_animations(self):
            animation.stop()
        self._set_fade_progress(1.0)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _update_display(self) -> None:
        """Update badge text and styling."""
        self.setText(self._name)

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
        changed = status != self._status
        self._status = status
        self._update_display()

        if tooltip:
            self.setToolTip(tooltip)

        # A re-report of the status already shown is not a state change, and
        # fading for one would make a polling probe pulse once a second.
        if changed:
            self._fade_through()

    def set_name(self, name: str) -> None:
        """Update the display name.

        Args:
            name: New display name
        """
        changed = name != self._name
        self._name = name
        self._update_display()
        if changed:
            self._fade_through()

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

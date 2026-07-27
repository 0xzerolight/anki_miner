"""Modern button widget with multiple style variants."""

# pyqtProperty is present at runtime but missing from the PyQt6 stubs.
from PyQt6.QtCore import QRectF, Qt, pyqtProperty  # type: ignore[attr-defined]
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPalette
from PyQt6.QtWidgets import QApplication, QPushButton

from anki_miner.gui.resources.styles import BORDER_RADIUS, MOTION
from anki_miner.gui.utils import motion

#: Strength of the press tint at full press. Measured across all 29 shipped
#: themes: this lands every variant's pressed step between 1.22 and 1.62 WCAG
#: contrast against its own resting colour, which is inside the range the theme
#: authors chose for their own ``primary-pressed`` values.
_PRESS_OVERLAY_ALPHA = 0.15

#: Below this HSL lightness a darkening tint stops being visible. The shipped
#: dark-theme backgrounds top out at 0.21 and the shipped accents start at 0.31,
#: so the threshold separates them with margin on both sides.
_DARK_SURFACE_LIGHTNESS = 0.25

#: Fraction of the tint applied synchronously on press, before Qt dispatches the
#: click. A handler is free to block the GUI thread outright, in which case no
#: animation frame ever lands -- so the first step of the feedback is painted
#: rather than scheduled, and only the remainder is animated.
_PRESS_FLOOR = 0.4

#: Variants that paint no background of their own and therefore show the page.
_TRANSPARENT_VARIANTS = frozenset({"secondary", "ghost"})


def press_overlay(surface: QColor | None) -> QColor:
    """Return the colour a fully-pressed control lays over ``surface``.

    Args:
        surface: The colour behind the tint, or ``None`` for a control that
            paints its own opaque accent fill. Every one of the 29 shipped
            themes authors its pressed accent *darker* than the base accent, so
            an accent fill always darkens and needs no measurement.

    Returns:
        A translucent black or white -- black darkens, which is the house
        convention, except on a near-black surface where it would be invisible.
    """
    lighten = surface is not None and surface.lightnessF() < _DARK_SURFACE_LIGHTNESS
    overlay = QColor(255, 255, 255) if lighten else QColor(0, 0, 0)
    overlay.setAlphaF(_PRESS_OVERLAY_ALPHA)
    return overlay


class ModernButton(QPushButton):
    """Enhanced button widget with style variants.

    Variants:
    - primary: Solid primary color background (default)
    - secondary: Outlined with primary border
    - ghost: Transparent with subtle hover
    - danger: Red for destructive actions

    Pressing paints a short tint over whatever the stylesheet drew. It is an
    overlay rather than a repaint because ``common.qss`` owns the button's
    colours: the two exist side by side instead of one re-deriving the other.
    The corresponding static ``QPushButton:pressed`` swap is neutralised for
    this class in ``common.qss`` -- it only ever reached the primary variant
    anyway, and an instant swap underneath an animated tint is two answers to
    one press.
    """

    def __init__(self, text: str = "", variant: str = "primary", parent=None):
        """Initialize the modern button.

        Args:
            text: Button text
            variant: Button variant ('primary', 'secondary', 'ghost', 'danger')
            parent: Optional parent widget
        """
        super().__init__(text, parent)

        # Apply variant styling
        self.setObjectName(variant)

        # Set minimum size for better touch targets
        self.setMinimumHeight(36)

        # Set accessibility properties
        self.setAccessibleName(text if text else self.tr("Button"))

        self._press_progress = 0.0
        # Qt's own signals, not overridden key/mouse handlers: `pressed` already
        # fires for the mouse and for whichever keys Qt considers activating for
        # this button (Space always, Return only when it is the default). These
        # connections are made in the constructor, so they run before anything a
        # caller connects later -- which is what puts the tint on screen ahead of
        # the click work.
        self.pressed.connect(self._begin_press)
        self.released.connect(self._end_press)

    # ------------------------------------------------------------------
    # Press feedback
    # ------------------------------------------------------------------
    def _get_press_progress(self) -> float:
        return self._press_progress

    def _set_press_progress(self, value: float) -> None:
        self._press_progress = value
        self.update()

    pressProgress = pyqtProperty(float, fget=_get_press_progress, fset=_set_press_progress)  # noqa: N815 - Qt property

    def press_overlay_colour(self) -> QColor:
        """Return this button's press tint at full press.

        The filled variants cover their own pixels with an accent, so the page
        colour behind them says nothing about what will be visible on top. The
        transparent variants *are* the page, and the page is near-black in over
        half the shipped themes.

        The page colour comes from the application palette rather than
        ``self.palette()``: Qt's stylesheet style writes a widget's own palette
        from the QSS that matched it, so a ``background: transparent`` variant
        reports a fully transparent Window role -- read as pitch black, which
        inverts the choice on every light theme.
        """
        if self.objectName() in _TRANSPARENT_VARIANTS:
            return press_overlay(QApplication.palette().color(QPalette.ColorRole.Window))
        return press_overlay(None)

    def _begin_press(self) -> None:
        """Tint immediately, then ease to full -- see ``_PRESS_FLOOR``."""
        self._set_press_progress(_PRESS_FLOOR)
        self.repaint()
        motion.animate(self, b"pressProgress", 1.0, duration=MOTION.press, curve=motion.colour_curve())

    def _end_press(self) -> None:
        motion.animate(self, b"pressProgress", 0.0, duration=MOTION.press, curve=motion.colour_curve())

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        """Draw the stylesheet's button, then the press tint over it."""
        super().paintEvent(event)
        if self._press_progress <= 0.0:
            return

        overlay = self.press_overlay_colour()
        overlay.setAlphaF(overlay.alphaF() * self._press_progress)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(overlay)
        radius = float(BORDER_RADIUS.default)
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        painter.end()

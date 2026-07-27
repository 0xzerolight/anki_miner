"""Modern button widget with multiple style variants."""

from typing import Literal

# pyqtProperty is present at runtime but missing from the PyQt6 stubs.
from PyQt6.QtCore import QEvent, QRectF, Qt, pyqtProperty  # type: ignore[attr-defined]
from PyQt6.QtGui import QColor, QHideEvent, QPainter, QPaintEvent, QPalette
from PyQt6.QtWidgets import QApplication, QPushButton

from anki_miner.gui.resources.styles import BORDER_RADIUS, MOTION
from anki_miner.gui.utils import motion
from anki_miner.gui.widgets.base.sizing import apply_button_size

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

#: Roles whose resting rule in ``common.qss`` paints no background, so the page
#: shows through them. Under D41 that is every role except the two fills:
#: ``primary`` (accent) and ``critical`` (solid red). ``danger`` belongs here —
#: it became a red *outline* over a transparent box.
_TRANSPARENT_VARIANTS = frozenset({"secondary", "ghost", "danger"})


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


#: The five roles a button can carry. Anything outside this list is a typo, and
#: renders as the ordinary quiet button.
ButtonVariant = Literal["primary", "secondary", "ghost", "danger", "critical"]


class ModernButton(QPushButton):
    """Enhanced button widget with style variants.

    Variants (D41 — accent is a scarce signal, red means destruction):

    - ``primary``: the one task action on a screen. Solid accent.
    - ``secondary``: the ordinary quiet control. Neutral text on a bordered box.
    - ``ghost``: quiet to the point of losing its border.
    - ``danger``: red *outline*, for reversible removals.
    - ``critical``: solid red. Reserved for the two irreversible actions in the
      app — deleting a settings profile and resetting the user Known Words list.

    Only ``primary`` stays eligible to become a dialog's automatic default, so
    Enter lands on the task action rather than on whichever quiet button
    happened to be built first. A button that is *explicitly* made the default
    still renders with the accent fill, because that is what Enter will press.

    Pressing paints a short tint over whatever the stylesheet drew. It is an
    overlay rather than a repaint because ``common.qss`` owns the button's
    colours: the two exist side by side instead of one re-deriving the other.
    The corresponding static ``:pressed`` swaps are neutralised for this class
    in ``common.qss`` — an instant swap underneath an animated tint is two
    answers to one press. Under D41 the swaps that reach this class are the
    base one (``secondary`` and any unmarked button) and the accent one
    (``primary`` and whatever Qt made the default).
    """

    def __init__(
        self,
        text: str = "",
        variant: ButtonVariant = "primary",
        parent=None,
        *,
        square: bool = False,
    ):
        """Initialize the modern button.

        Args:
            text: Button text
            variant: Button role — see the class docstring
            parent: Optional parent widget
            square: Pin the width to the metric height, for glyph-only controls
                such as the chain-editor reorder arrows and its trash. A glyph
                button that stretches to a text button's width is how the four
                chain editors ended up with two full-width arrows (D13).
        """
        super().__init__(text, parent)

        # Apply variant styling
        self.setObjectName(variant)

        # A quiet button must not silently become the Enter target of a dialog
        # just by being constructed first. Qt only auto-promotes buttons whose
        # autoDefault is on, so declining it here is what moves Enter onto the
        # primary action; an explicit setDefault() still works and still paints
        # accent through the QSS `:default` rules.
        if variant != "primary":
            self.setAutoDefault(False)

        # A floor measured through the rendered font, not the 36px constant this
        # replaces: 36 was slack at 100% text and under the glyphs at 150%, so it
        # made every button taller than its content on the machine the number was
        # chosen on, and shorter than its content on everybody else's.
        apply_button_size(self, square=square)

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

    def paints_its_own_fill(self) -> bool:
        """Whether ``common.qss`` currently gives this button an opaque fill.

        The role is only half the answer under D41: ``:checked`` and
        ``:default`` both hand a quiet variant the accent fill, and those two
        rules outrank the transparent resting rule they replace. A button in
        either state is filled whatever it was constructed as.
        """
        return self.objectName() not in _TRANSPARENT_VARIANTS or self.isChecked() or self.isDefault()

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
        if self.paints_its_own_fill():
            return press_overlay(None)
        return press_overlay(QApplication.palette().color(QPalette.ColorRole.Window))

    def _begin_press(self) -> None:
        """Tint immediately, then ease to full -- see ``_PRESS_FLOOR``."""
        self._set_press_progress(_PRESS_FLOOR)
        self.repaint()
        motion.animate(self, b"pressProgress", 1.0, duration=MOTION.press, curve=motion.colour_curve())

    def _end_press(self) -> None:
        motion.animate(self, b"pressProgress", 0.0, duration=MOTION.press, curve=motion.colour_curve())

    def changeEvent(self, event: QEvent | None) -> None:  # noqa: N802 - Qt override
        """Release the tint if the button is disabled while held.

        Qt drops the ``down`` state on disable without emitting ``released``,
        and a disabled widget receives no mouse release either -- so without
        this the tint would still be there when the button comes back.
        """
        super().changeEvent(event)
        if event is not None and event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._end_press()

    def hideEvent(self, event: QHideEvent | None) -> None:  # noqa: N802 - Qt override
        """Drop the tint outright: nobody is watching a hidden button fade."""
        super().hideEvent(event)
        for animation in motion.active_animations(self):
            animation.stop()
        self._set_press_progress(0.0)

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

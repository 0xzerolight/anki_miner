"""Per-word audio clip window editor for the word curator.

One always-visible row: a two-handle slider for the clip window, with the
length drawn inside it, and a button that plays just that clip. Dragging rather
than typing timestamps because this is never a precise edit — it is a nudge for
a line that got cut off or ran into the next speaker — and the row is small
enough that hiding it behind a disclosure only cost a click and made it
undiscoverable.

The widget owns no media and no config. It is told a word's window
(:meth:`set_word`) and reports edits back through signals; the dialog decides
what to do with them and the player performs the preview.

Windows are absolute seconds on the source video's own timeline, the same units
the player seeks in and ffmpeg cuts with, so nothing is converted between what
the user sees and what the card gets. Internally everything is integer *ticks*
of :data:`TICK_SECONDS`, because the slider is an integer control.
"""

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.utils.i18n import tr_format

#: Shortest window the slider will produce. Matches
#: ``media_extractor.MIN_CLIP_SECONDS`` — the two ends of the same contract.
MIN_CLIP_SECONDS = 0.2

#: Longest window the slider will produce. No vocabulary card wants a clip
#: longer than this, and a long line plus its slack could otherwise reach it.
MAX_CLIP_SECONDS = 30.0

#: Resolution of one keyboard step, in seconds. Also the readout's precision.
TICK_SECONDS = 0.1

#: How far past the seeded window the handles can travel, in seconds. Fixed per
#: word: travel must not move under the pointer mid-drag, which is exactly what
#: the two interlocked spinboxes this replaced used to do.
MARGIN_SECONDS = 3.0

_MIN_TICKS = round(MIN_CLIP_SECONDS / TICK_SECONDS)
_MAX_TICKS = round(MAX_CLIP_SECONDS / TICK_SECONDS)
_MARGIN_TICKS = round(MARGIN_SECONDS / TICK_SECONDS)


def to_ticks(seconds: float) -> int:
    """Quantise seconds onto the slider's integer grid."""
    return round(seconds / TICK_SECONDS)


def to_seconds(ticks: int) -> float:
    """Return a tick count as seconds, at the grid's own precision."""
    return round(ticks * TICK_SECONDS, 1)


def coerce(in_ticks: int, out_ticks: int, lo: int, hi: int, *, moved_in: bool) -> tuple[int, int]:
    """Return a legal ``(in, out)`` after the user moved one handle.

    The handle the user moved keeps its position wherever possible and the
    other one is pushed, rather than the moved handle being snapped back: a
    handle that refuses to follow the pointer reads as broken. ``lo``/``hi``
    bound both; the length stays within MIN/MAX.
    """
    if moved_in:
        in_ticks = max(lo, min(in_ticks, hi - _MIN_TICKS))
        out_ticks = min(hi, max(out_ticks, in_ticks + _MIN_TICKS))
        out_ticks = min(out_ticks, in_ticks + _MAX_TICKS)
        # The hi clamp may have shortened the push below MAX; pull in back.
        in_ticks = max(in_ticks, out_ticks - _MAX_TICKS)
    else:
        out_ticks = min(hi, max(out_ticks, lo + _MIN_TICKS))
        in_ticks = max(lo, min(in_ticks, out_ticks - _MIN_TICKS))
        in_ticks = max(in_ticks, out_ticks - _MAX_TICKS)
        out_ticks = min(out_ticks, in_ticks + _MAX_TICKS)
    return in_ticks, out_ticks


#: Painted metrics, in device-independent pixels.
_HANDLE_WIDTH = 8

#: Padding above and below the readout inside the bar. The bar is sized from
#: the font rather than fixed: the length is drawn *on* it, and a bar shorter
#: than the text spills the glyphs onto the page behind, where they vanish
#: against a light theme's background.
_GROOVE_PADDING = 4


class ClipRangeSlider(QWidget):
    """A two-handle range control that draws its own length readout.

    Self-painted rather than a QSlider subclass: QStyle draws exactly one
    handle, and the app ships no QSS for QSlider at all. Colours come from the
    active theme's own variables, so this control follows every theme without
    a stylesheet rule — the QApplication palette is only the fallback.

    Everything here is in integer ticks. Seconds, defaults and clip-length
    limits belong to the host.
    """

    #: The user moved a handle. Payload is ``(in, out)`` in ticks.
    values_changed = pyqtSignal(int, int)

    #: The user double-clicked, asking for the default window back.
    reset_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lo = 0
        self._hi = 0
        self._in = 0
        self._out = 0
        self._text = ""
        # Which handle the next drag or arrow key moves. Sticky after a drag so
        # the keyboard keeps nudging the end the user was just working on.
        self._active_out = False
        self._dragging = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(self._bar_height() + _GROOVE_PADDING)
        self.setMinimumWidth(80)

    # ------------------------------------------------------------------
    # Host API
    # ------------------------------------------------------------------

    def set_span(self, lo: int, hi: int) -> None:
        """Set the travel both handles move within, pulling the values inside it."""
        self._lo, self._hi = lo, max(lo, hi)
        self.set_values(self._in, self._out)

    def set_values(self, in_ticks: int, out_ticks: int) -> None:
        """Place the handles. Never emits — the host writes back mid-drag."""
        self._in = min(max(in_ticks, self._lo), self._hi)
        self._out = min(max(out_ticks, self._in), self._hi)
        self.update()

    def values(self) -> tuple[int, int]:
        """The handle positions, in ticks."""
        return self._in, self._out

    def set_text(self, text: str) -> None:
        """Set the readout drawn on the selected span."""
        self._text = text
        self.update()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _bar_height(self) -> int:
        """The bar's thickness: whatever it takes to hold the readout."""
        return self.fontMetrics().height() + _GROOVE_PADDING

    def _groove_rect(self) -> QRectF:
        """The bar the handles travel along, inset so a handle never clips out."""
        inset = _HANDLE_WIDTH / 2
        height = min(self._bar_height(), self.height())
        top = (self.height() - height) / 2
        return QRectF(inset, top, max(0.0, self.width() - _HANDLE_WIDTH), height)

    def _pos_for(self, ticks: int) -> float:
        """The x centre, in pixels, for a tick value."""
        groove = self._groove_rect()
        if self._hi == self._lo:
            return groove.left()
        fraction = (ticks - self._lo) / (self._hi - self._lo)
        return groove.left() + fraction * groove.width()

    def _ticks_for(self, x: float) -> int:
        """The tick value for an x in pixels, clamped to the span."""
        groove = self._groove_rect()
        if self._hi == self._lo or groove.width() <= 0:
            return self._lo
        fraction = (x - groove.left()) / groove.width()
        ticks = self._lo + round(fraction * (self._hi - self._lo))
        return min(max(ticks, self._lo), self._hi)

    def _handle_rect(self, ticks: int) -> QRectF:
        centre = self._pos_for(ticks)
        return QRectF(centre - _HANDLE_WIDTH / 2, 0.0, float(_HANDLE_WIDTH), float(self.height()))

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _move_active(self, ticks: int) -> None:
        """Move the active handle, stopping it at the other one."""
        if self._active_out:
            self._out = min(max(ticks, self._in), self._hi)
        else:
            self._in = min(max(ticks, self._lo), self._out)
        self.update()
        self.values_changed.emit(self._in, self._out)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None or not self.isEnabled() or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = event.position().x()
        # Whichever end is nearer, so the whole bar is a drag target rather
        # than two eight-pixel ones.
        self._active_out = abs(x - self._pos_for(self._out)) < abs(x - self._pos_for(self._in))
        self._dragging = True
        self._move_active(self._ticks_for(x))

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None or not self._dragging:
            super().mouseMoveEvent(event)
            return
        self._move_active(self._ticks_for(event.position().x()))

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt override
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None or not self.isEnabled() or event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        # The only way back to the default window, now that the strip has no
        # reset button. The tooltip is where the user is told so.
        self._dragging = False
        self.reset_requested.emit()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None:
            super().keyPressEvent(event)
            return
        step = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Down: -1,
            Qt.Key.Key_Up: 1,
            Qt.Key.Key_PageDown: -5,
            Qt.Key.Key_PageUp: 5,
        }.get(Qt.Key(event.key()))
        if step is None or not self.isEnabled():
            super().keyPressEvent(event)
            return
        current = self._out if self._active_out else self._in
        self._move_active(current + step)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _themed(self, key: str, role: QPalette.ColorRole) -> QColor:
        """The theme's colour for ``key``, falling back to a palette role.

        The palette's own Highlight is the *selection* colour, which in the
        light themes is a pale wash — legible behind text, invisible as a
        filled bar. The accent has to come from the same ``primary`` the rest
        of the app draws with.
        """
        colour = QColor(Theme.get_colors().get(key, ""))
        return colour if colour.isValid() else self.palette().color(role)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        groove = self._groove_rect()
        radius = groove.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(self._themed("border", QPalette.ColorRole.Mid))
        painter.drawRoundedRect(groove, radius, radius)

        accent = (
            self._themed("primary", QPalette.ColorRole.Highlight)
            if enabled
            else self._themed("disabled", QPalette.ColorRole.Mid)
        )
        span = QRectF(groove)
        span.setLeft(self._pos_for(self._in))
        span.setRight(self._pos_for(self._out))
        painter.setBrush(accent)
        painter.drawRoundedRect(span, radius, radius)

        # A darker shade, not the accent again: a grip the same colour as the
        # bar it sits on is not a grip, and the ends are what the user aims at.
        painter.setBrush(
            self._themed("primary-pressed", QPalette.ColorRole.Shadow)
            if enabled
            else self._themed("disabled", QPalette.ColorRole.Mid)
        )
        for ticks in (self._in, self._out):
            painter.drawRoundedRect(self._handle_rect(ticks), _HANDLE_WIDTH / 2, _HANDLE_WIDTH / 2)

        if self._text:
            # Centred on the filled span when the span is wide enough to hold
            # it, on the whole widget otherwise — a short clip's span is
            # narrower than "2.6 s", and text half-on the bar would have its
            # overhang disappear against a light theme's background.
            metrics = painter.fontMetrics()
            fits = span.width() >= metrics.horizontalAdvance(self._text) + _HANDLE_WIDTH * 2
            target = span if fits else QRectF(self.rect())
            painter.setPen(
                self._themed("text-on-primary", QPalette.ColorRole.HighlightedText)
                if fits
                else self._themed("text", QPalette.ColorRole.Text)
            )
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, self._text)


class AudioClipEditor(QWidget):
    """Always-visible editor for one word's audio clip window."""

    #: A window was edited. Payload is the absolute ``(in, out)`` in seconds.
    clip_changed = pyqtSignal(float, float)

    #: The user asked for the default window back (no override).
    clip_reset = pyqtSignal()

    #: Preview requested for the current window: ``(in, out)`` in seconds.
    play_requested = pyqtSignal(float, float)

    #: Preview should stop (the play button was pressed while playing).
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The word's untouched window in ticks, kept so a reset can restore it
        # and so an edit can be recognised as one. Ticks, not seconds: a line
        # starting at 5.03 s quantises to 5.0, and comparing that against the
        # unquantised default would report an override the user never made.
        # None before the first set_word.
        self._default: tuple[int, int] | None = None
        # The last window this class put on the slider, in ticks. It is the
        # before-picture ``_on_slider_moved`` compares against.
        self._last: tuple[int, int] = (0, 0)
        # Suppresses clip_changed while set_word moves the handles: seeding a
        # control is not the user editing it, and a spurious signal would
        # record an override for every word the user merely scrolled past.
        self._seeding = False
        self._playing = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        self.slider = ClipRangeSlider()
        self.slider.setToolTip(
            self.tr(
                "Drag either end to trim this word's audio clip; double-click to restore the default. "
                "Only this word is affected."
            )
        )
        self.slider.values_changed.connect(self._on_slider_moved)
        self.slider.reset_requested.connect(self._on_reset)
        row.addWidget(self.slider, 1)

        # The clip preview lives next to the window it plays, deliberately
        # separate from the player's own transport below the video: that one
        # plays the scene, this one plays the card's audio.
        self.play_button = QToolButton()
        self.play_button.setAutoRaise(True)
        self.play_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.play_button.clicked.connect(self._on_play_clicked)
        row.addWidget(self.play_button)

        self._refresh_play_button()
        self._refresh_readout()

    # ------------------------------------------------------------------
    # Host API
    # ------------------------------------------------------------------

    def set_word(self, start: float, end: float, padding: float, override: tuple[float, float] | None) -> None:
        """Show the window for a word, seeding the handles with what will be cut.

        Args:
            start: Subtitle line start, in seconds.
            end: Subtitle line end, in seconds.
            padding: ``config.audio_padding`` — what the default window widens by.
            override: The user's window for this word, or None for the default.

        The slider always states the window ffmpeg would produce right now.
        Seeding never emits ``clip_changed`` and never applies MIN/MAX: an
        over-long default belongs to the line, not to an edit the user made.
        """
        default = (to_ticks(max(0.0, start - padding)), to_ticks(end + padding))
        self._default = default
        current = (to_ticks(override[0]), to_ticks(override[1])) if override is not None else default
        self.setEnabled(True)

        self._seeding = True
        try:
            self._seat(current)
        finally:
            self._seeding = False
        self._refresh_readout()

    def clear_word(self) -> None:
        """Show the strip as having nothing to edit (no word focused)."""
        self._default = None
        self._seeding = True
        try:
            self.slider.set_span(0, 0)
            self._write(0, 0)
        finally:
            self._seeding = False
        self.setEnabled(False)
        self._refresh_readout()

    def set_playing(self, playing: bool) -> None:
        """Reflect whether the clip preview is currently running."""
        self._playing = playing
        self._refresh_play_button()

    def current_window(self) -> tuple[float, float]:
        """The window the handles currently state, in seconds."""
        in_ticks, out_ticks = self.slider.values()
        return to_seconds(in_ticks), to_seconds(out_ticks)

    def has_override(self) -> bool:
        """Whether the current window differs from the word's default."""
        if self._default is None:
            return False
        return self.slider.values() != self._default

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _seat(self, window: tuple[int, int]) -> None:
        """Give the slider its per-word travel and put the handles on it."""
        lo = max(0, window[0] - _MARGIN_TICKS)
        hi = window[1] + _MARGIN_TICKS
        self.slider.set_span(lo, hi)
        self._write(*window)

    def _write(self, in_ticks: int, out_ticks: int) -> None:
        """Place the handles and remember where, for the next move's comparison."""
        self.slider.set_values(in_ticks, out_ticks)
        self._last = self.slider.values()

    def _on_slider_moved(self, in_ticks: int, out_ticks: int) -> None:
        if self._seeding or self._default is None:
            return
        # Which handle moved is read against the last window *this* class wrote,
        # not against the slider: the slider has already moved itself by the
        # time it emits, so its own values no longer hold the before-picture.
        moved_in = in_ticks != self._last[0]
        in_ticks, out_ticks = coerce(in_ticks, out_ticks, self.slider._lo, self.slider._hi, moved_in=moved_in)
        self._write(in_ticks, out_ticks)
        self._refresh_readout()
        self.clip_changed.emit(*self.current_window())

    def _on_play_clicked(self) -> None:
        if self._playing:
            self.stop_requested.emit()
            return
        self.play_requested.emit(*self.current_window())

    def _on_reset(self) -> None:
        if self._default is None:
            return
        self._seeding = True
        try:
            self._seat(self._default)
        finally:
            self._seeding = False
        self._refresh_readout()
        self.clip_reset.emit()

    def _refresh_play_button(self) -> None:
        self.play_button.setText("■" if self._playing else "▶")
        self.play_button.setToolTip(
            self.tr("Stop the clip preview") if self._playing else self.tr("Play just this clip")
        )

    def _refresh_readout(self) -> None:
        """Draw the clip length on the slider, or nothing when no word is focused."""
        if self._default is None:
            self.slider.set_text("")
            return
        in_value, out_value = self.current_window()
        self.slider.set_text(tr_format(self.tr("%1 s"), f"{out_value - in_value:.1f}"))

"""Per-word audio clip window editor for the word curator.

A single collapsed strip that expands into two timestamps, a length readout, a
playback button and a reset. It is deliberately small: editing a clip window is
a niche repair for a line that got cut off or ran into the next speaker, and it
must not compete for space with the word table, sentence picker or dictionary
it sits beside. Collapsed is the default, and the expanded body is one row.

The widget owns no media and no config. It is told a word's window
(:meth:`set_word`) and reports edits back through signals; the dialog decides
what to do with them and the player performs the preview.
"""

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.utils.i18n import tr_format

#: Shortest window the spinboxes will produce. Matches
#: ``media_extractor.MIN_CLIP_SECONDS`` — the two ends of the same contract.
MIN_CLIP_SECONDS = 0.2

#: Longest window the spinboxes will produce. A slipped decimal point in a
#: timestamp is the realistic way to ask for a twenty-minute mp3 by accident,
#: and no vocabulary card wants a clip longer than this.
MAX_CLIP_SECONDS = 30.0

#: Step for one arrow-key press or wheel notch, in seconds.
_STEP_SECONDS = 0.1

#: Painted metrics, in device-independent pixels.
_HANDLE_WIDTH = 8
_GROOVE_HEIGHT = 8
_WIDGET_HEIGHT = 22


class ClipRangeSlider(QWidget):
    """A two-handle range control that draws its own length readout.

    Self-painted rather than a QSlider subclass: QStyle draws exactly one
    handle, and the app ships no QSS for QSlider at all. Colours come from the
    palette because a theme routes the whole QApplication palette, so this
    control follows all of them without a stylesheet rule.

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
        self.setMinimumHeight(_WIDGET_HEIGHT)
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

    def _groove_rect(self) -> QRectF:
        """The bar the handles travel along, inset so a handle never clips out."""
        inset = _HANDLE_WIDTH / 2
        top = (self.height() - _GROOVE_HEIGHT) / 2
        return QRectF(inset, top, max(0.0, self.width() - _HANDLE_WIDTH), _GROOVE_HEIGHT)

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

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        enabled = self.isEnabled()
        groove = self._groove_rect()
        radius = groove.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(palette.color(QPalette.ColorRole.Mid))
        painter.drawRoundedRect(groove, radius, radius)

        accent = palette.color(QPalette.ColorRole.Highlight if enabled else QPalette.ColorRole.Mid)
        span = QRectF(groove)
        span.setLeft(self._pos_for(self._in))
        span.setRight(self._pos_for(self._out))
        painter.setBrush(accent)
        painter.drawRoundedRect(span, radius, radius)
        for ticks in (self._in, self._out):
            painter.drawRoundedRect(self._handle_rect(ticks), radius, radius)

        if self._text:
            # On the span when it fits, otherwise centred on the widget: a
            # short clip's span is narrower than "2.6 s".
            metrics = painter.fontMetrics()
            fits = span.width() >= metrics.horizontalAdvance(self._text) + _HANDLE_WIDTH * 2
            target = span if fits else QRectF(self.rect())
            role = QPalette.ColorRole.HighlightedText if fits else QPalette.ColorRole.Text
            painter.setPen(palette.color(role))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, self._text)


class AudioClipEditor(QWidget):
    """Collapsible editor for one word's audio clip window.

    Timestamps are absolute seconds on the source video's own timeline, the
    same units the player seeks in and ffmpeg cuts with, so nothing is
    converted between what the user reads here and what the card gets.
    """

    #: A window was edited. Payload is the absolute ``(in, out)`` in seconds.
    clip_changed = pyqtSignal(float, float)

    #: The user asked for the default window back (no override).
    clip_reset = pyqtSignal()

    #: Preview requested for the current window: ``(in, out)`` in seconds.
    play_requested = pyqtSignal(float, float)

    #: Preview should stop (the play button was pressed while playing).
    stop_requested = pyqtSignal()

    #: The strip was expanded (True) or collapsed (False). Persisted by the host.
    expanded_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The word's untouched window, kept so Reset can restore it and so an
        # edit can be recognised as one. None before the first set_word.
        self._default: tuple[float, float] | None = None
        # Suppresses clip_changed while set_word writes the spinboxes: seeding
        # a control is not the user editing it, and a spurious signal would
        # record an override for every word the user merely scrolled past.
        self._seeding = False
        self._playing = False
        self._setup_ui()
        self._set_expanded(False)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACING.xxs)

        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_button.setToolTip(
            self.tr("Trim this word's audio clip. Only this word is affected; every other card keeps the default.")
        )
        self.toggle_button.toggled.connect(self._set_expanded)
        outer.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.body = QWidget()
        row = QHBoxLayout(self.body)
        row.setContentsMargins(SPACING.md, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        self.in_label = QLabel(self.tr("In"))
        row.addWidget(self.in_label)
        self.in_spin = self._make_spin()
        self.in_spin.valueChanged.connect(self._on_in_changed)
        row.addWidget(self.in_spin)

        self.out_label = QLabel(self.tr("Out"))
        row.addWidget(self.out_label)
        self.out_spin = self._make_spin()
        self.out_spin.valueChanged.connect(self._on_out_changed)
        row.addWidget(self.out_spin)

        self.length_label = QLabel()
        self.length_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        row.addWidget(self.length_label)

        # The clip preview lives next to the window it plays, deliberately
        # separate from the player's own transport below the video: that one
        # plays the scene, this one plays the card's audio.
        self.play_button = QToolButton()
        self.play_button.setAutoRaise(True)
        self.play_button.clicked.connect(self._on_play_clicked)
        row.addWidget(self.play_button)

        self.reset_button = QToolButton()
        self.reset_button.setAutoRaise(True)
        self.reset_button.setText("↺")
        self.reset_button.setToolTip(self.tr("Restore the default clip length for this word"))
        self.reset_button.clicked.connect(self._on_reset_clicked)
        row.addWidget(self.reset_button)

        row.addStretch()
        outer.addWidget(self.body)

        self._refresh_play_button()
        self._refresh_labels()

    def _make_spin(self) -> QDoubleSpinBox:
        """A seconds spinbox: one decimal, arrow/wheel nudge, no thousands sep."""
        spin = QDoubleSpinBox()
        spin.setDecimals(1)
        spin.setSingleStep(_STEP_SECONDS)
        spin.setSuffix(self.tr(" s"))
        spin.setGroupSeparatorShown(False)
        spin.setKeyboardTracking(False)
        spin.setMinimum(0.0)
        spin.setMaximum(0.0)
        return spin

    # ------------------------------------------------------------------
    # Host API
    # ------------------------------------------------------------------

    def set_word(self, start: float, end: float, padding: float, override: tuple[float, float] | None) -> None:
        """Show the window for a word, seeding the fields with what will be cut.

        Args:
            start: Subtitle line start, in seconds.
            end: Subtitle line end, in seconds.
            padding: ``config.audio_padding`` — what the default window widens by.
            override: The user's window for this word, or None for the default.

        The fields always state the window ffmpeg would produce right now, so a
        user who expands the strip sees the real numbers rather than zeroes to
        interpret. Seeding never emits ``clip_changed``.
        """
        default = (max(0.0, start - padding), end + padding)
        self._default = default
        current = override if override is not None else default
        self.setEnabled(True)

        self._seeding = True
        try:
            # Bounds first, then values: a value outside the current bounds
            # would be silently clamped on the way in.
            self._apply_bounds(current)
            self.in_spin.setValue(current[0])
            self.out_spin.setValue(current[1])
        finally:
            self._seeding = False
        self._refresh_labels()

    def clear_word(self) -> None:
        """Show the strip as having nothing to edit (no word focused)."""
        self._default = None
        self._seeding = True
        try:
            self.in_spin.setValue(0.0)
            self.out_spin.setValue(0.0)
        finally:
            self._seeding = False
        self.setEnabled(False)
        self._refresh_labels()

    def set_playing(self, playing: bool) -> None:
        """Reflect whether the clip preview is currently running."""
        self._playing = playing
        self._refresh_play_button()

    @property
    def expanded(self) -> bool:
        """Whether the body is showing."""
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse the strip (restores a remembered state)."""
        self.toggle_button.setChecked(expanded)

    def current_window(self) -> tuple[float, float]:
        """The window the fields currently state, in seconds."""
        return self.in_spin.value(), self.out_spin.value()

    def has_override(self) -> bool:
        """Whether the current window differs from the word's default."""
        if self._default is None:
            return False
        return self.current_window() != self._default

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_bounds(self, window: tuple[float, float]) -> None:
        """Constrain each spinbox against the other and the length limits.

        Enforced as spinbox ranges rather than after-the-fact correction: the
        user can never see, or commit, a window with the out before the in.
        """
        in_value, out_value = window
        self.in_spin.setMinimum(max(0.0, out_value - MAX_CLIP_SECONDS))
        self.in_spin.setMaximum(max(0.0, out_value - MIN_CLIP_SECONDS))
        self.out_spin.setMinimum(in_value + MIN_CLIP_SECONDS)
        self.out_spin.setMaximum(in_value + MAX_CLIP_SECONDS)

    def _on_in_changed(self, value: float) -> None:
        self._on_edited((value, self.out_spin.value()))

    def _on_out_changed(self, value: float) -> None:
        self._on_edited((self.in_spin.value(), value))

    def _on_edited(self, window: tuple[float, float]) -> None:
        if self._seeding:
            return
        self._apply_bounds(window)
        self._refresh_labels()
        self.clip_changed.emit(*window)

    def _on_play_clicked(self) -> None:
        if self._playing:
            self.stop_requested.emit()
            return
        self.play_requested.emit(*self.current_window())

    def _on_reset_clicked(self) -> None:
        if self._default is None:
            return
        self._seeding = True
        try:
            self._apply_bounds(self._default)
            self.in_spin.setValue(self._default[0])
            self.out_spin.setValue(self._default[1])
        finally:
            self._seeding = False
        self._refresh_labels()
        self.clip_reset.emit()

    def _set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.body.setVisible(expanded)
        self.expanded_changed.emit(expanded)

    def _refresh_play_button(self) -> None:
        self.play_button.setText("■" if self._playing else "▶")
        self.play_button.setToolTip(
            self.tr("Stop the clip preview") if self._playing else self.tr("Play just this clip")
        )

    def _refresh_labels(self) -> None:
        """Repaint the header and the length readout from the current window."""
        edited = self.has_override()
        self.toggle_button.setText(self.tr("Audio clip · edited") if edited else self.tr("Audio clip"))
        self.reset_button.setEnabled(edited)
        if self._default is None:
            self.length_label.clear()
            return
        in_value, out_value = self.current_window()
        self.length_label.setText(tr_format(self.tr("%1 s"), f"{out_value - in_value:.1f}"))

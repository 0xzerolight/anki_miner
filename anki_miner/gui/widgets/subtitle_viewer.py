"""Subtitle timing workbench: pick a line, nudge the offset, hear the result.

Decision D35-B. Manual timing is a direct-manipulation task, and this is the one
screen in the app where keyboard nudging with immediate audio feedback is the
entire point. Its predecessor played the video and offered a single number
field, so finding a 300 ms drift meant typing a number and guessing.

The shape:

- A list of the parsed subtitle lines. Picking one seeks playback to it.
- ``Space`` plays and pauses; ``Left``/``Right`` move the offset one step and
  immediately replay the selected line at the new value; ``A`` holds the
  original timing so the difference is audible.
- An overlay over the picture reads ``Offset +1.20 s``.
- ``Align automatically`` closes with its own result code so the caller can hand
  the pair to the existing Retime tool. This screen never aligns anything itself.

Three offsets exist and must not be confused. ``_initial_offset`` is what the
dialog opened with and is the A side of the comparison. ``_working_offset`` is
the user's current value and the only thing Apply commits. The *preview* offset
is whichever of the two the player is currently rendering — comparing changes
what you hear, never what you would keep.

Keyboard scope (D49). Bare ``Space``, ``A`` and ``Return`` are bound to
:attr:`SubtitleViewer.workbench` — the line list and the player — because
neither can hold a text cursor. The offset field sits outside it, so a Japanese
input method committing a composition there can never fire a shortcut. Apply is
also ``Ctrl+Return``/``Ctrl+Enter`` from anywhere in the dialog, and no button is
the dialog's default.

mpv lifetime. ``player_widget`` is built into its cell once and is never
reparented, animated or captured; every terminal path funnels through
:meth:`done`, which releases the core before the dialog closes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.resources.styles import BORDER_RADIUS, SPACING
from anki_miner.gui.utils.content_text import apply_content_font
from anki_miner.gui.utils.fonts import JAPANESE_BODY
from anki_miner.gui.utils.keyboard_shortcuts import primary_action_shortcut, scoped_shortcut
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton
from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

#: One press of Left or Right. 100 ms is about the smallest drift a listener
#: reliably hears, and ten presses cover the full second most desyncs are off by.
NUDGE_SECONDS = 0.10

#: Decimal places the offset is kept and shown to. The spinbox rounds to this,
#: so the stored value rounds to it too — otherwise repeated nudges leave the
#: readout and the committed value disagreeing in the third place.
_OFFSET_DECIMALS = 2

#: The overlay reads over the video, not over the app surface: its backdrop is
#: whatever frame is on screen, so it carries its own always-dark plate instead
#: of a theme colour. Nothing here is themable, and nothing here needs to be.
_OVERLAY_STYLE = f"""
QLabel#offset-overlay {{
    background-color: rgba(0, 0, 0, 178);
    color: #ffffff;
    border-radius: {BORDER_RADIUS.small}px;
    padding: {SPACING.xxs}px {SPACING.xs}px;
    margin: {SPACING.xs}px;
}}
"""


def _timestamp(seconds: float) -> str:
    """Format a subtitle start time as MM:SS for the line list."""
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


class SubtitleViewer(ScreenIssueHost, QDialog):
    """Preview a video against its subtitles and correct their timing by ear.

    Args:
        video_path: Path to the video file.
        subtitle_entries: List of (start_seconds, end_seconds, text) tuples,
            parsed with **no** offset applied — this dialog owns the offset.
        initial_offset: Offset the dialog opens with, in seconds.
        parent: Optional parent widget.
        audio_track_override: Optional 0-indexed audio track to force instead of
            auto-detecting Japanese. None preserves auto-detect (mpv metadata).
        audio_track_codes: The mining language's audio-track language codes,
            used for that auto-detect. None keeps the player's ja default.
    """

    #: ``exec()`` result meaning "hand this pair to the automatic aligner".
    #: Deliberately outside QDialog's own two codes so a caller cannot mistake
    #: it for Apply.
    ALIGN_REQUESTED = 2

    def __init__(
        self,
        video_path: Path,
        subtitle_entries: list[tuple[float, float, str]],
        initial_offset: float = 0.0,
        parent=None,
        *,
        audio_track_override: int | None = None,
        audio_track_codes: frozenset[str] | None = None,
        content_style: ContentTextStyle | None = None,
    ):
        super().__init__(parent)
        # The cue list and the player's strip are mined content (D45-B); None
        # keeps today's Japanese face.
        self._content_style = content_style or get_profile("ja").content_style
        self._entries: list[tuple[float, float, str]] = list(subtitle_entries)
        self._initial_offset = initial_offset
        self._working_offset = initial_offset
        self._comparing = False
        # True while this dialog is writing the spinbox itself, so the
        # valueChanged slot can tell a user edit from an echo of its own state.
        self._echoing_offset = False

        self.setWindowTitle(self.tr("Subtitle Timing Viewer"))
        self.setMinimumSize(860, 600)
        self.resize(960, 660)

        self._setup_ui(initial_offset)
        self._setup_shortcuts()
        add_min_max_buttons(self)

        # Set before set_source: that call is what triggers the mpv load whose
        # file-loaded event picks the audio track.
        if audio_track_codes is not None:
            self.player_widget.audio_track_codes = audio_track_codes

        self.player_widget.source_loaded.connect(self._on_source_loaded)
        self.player_widget.playback_failed.connect(self._on_playback_failed)

        self.player_widget.set_source(
            video_path,
            self._entries,
            initial_offset,
            audio_track_override=audio_track_override,
        )
        self._sync_readouts()
        self._select_initial_line()
        self._show_loading_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self, initial_offset: float) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(SPACING.xs, SPACING.xs, SPACING.xs, SPACING.xs)
        layout.setSpacing(SPACING.xs)

        layout.addWidget(self._create_workbench(), 1)

        # One line, two honest states: what the screen is doing while it loads,
        # and what the keys do once it can.
        self._hint_text = self.tr(
            "Space plays and pauses · Left and Right nudge 100 ms · A compares the original · Ctrl+Enter applies"
        )
        self.status_label = QLabel()
        self.status_label.setObjectName("helper-text")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addLayout(self._create_controls(initial_offset))
        self.setLayout(layout)

        # A playback failure is recoverable — the offset can still be typed and
        # applied — so it belongs in a banner, never in a modal (D24).
        self.install_issue_banner(layout)
        self._disown_default_buttons()

    def _create_workbench(self) -> QWidget:
        """Build the line list and the player: the bare-key-safe surface."""
        self.workbench = QWidget()
        row = QHBoxLayout(self.workbench)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        self.line_list = QListWidget()
        self.line_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.line_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.line_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.line_list.setUniformItemSizes(True)
        apply_content_font(self.line_list, self._content_style, role=JAPANESE_BODY)
        self._populate_lines()
        self.line_list.currentRowChanged.connect(self._on_line_selected)
        row.addWidget(self.line_list, 2)

        # The player and its overlay share one grid cell, so the readout sits
        # over the picture without the mpv surface being given a child or being
        # reparented into an overlay container.
        player_cell = QWidget()
        cell = QGridLayout(player_cell)
        cell.setContentsMargins(0, 0, 0, 0)

        self.player_widget = SubtitlePlayerWidget(content_style=self._content_style)
        cell.addWidget(self.player_widget, 0, 0)

        self.offset_overlay = QLabel()
        self.offset_overlay.setObjectName("offset-overlay")
        self.offset_overlay.setTextFormat(Qt.TextFormat.PlainText)
        self.offset_overlay.setStyleSheet(_OVERLAY_STYLE)
        cell.addWidget(
            self.offset_overlay,
            0,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        self.offset_overlay.raise_()

        row.addWidget(player_cell, 5)
        return self.workbench

    def _create_controls(self, initial_offset: float) -> QHBoxLayout:
        """Build the control row: hand-off, offset, comparison, exits."""
        controls = QHBoxLayout()
        controls.setSpacing(SPACING.xs)

        self.align_button = ModernButton(self.tr("Align automatically"), variant="secondary")
        self.align_button.setToolTip(
            self.tr("Hand this video and subtitle to the Retime tool, which matches them by audio.")
        )
        self.align_button.clicked.connect(self._on_align_clicked)
        controls.addWidget(self.align_button)

        controls.addStretch()

        controls.addWidget(QLabel(self.tr("Offset:")))

        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        self.offset_spinbox.setDecimals(_OFFSET_DECIMALS)
        self.offset_spinbox.setSingleStep(NUDGE_SECONDS)
        self.offset_spinbox.setValue(initial_offset)
        self.offset_spinbox.setSuffix(" s")
        self.offset_spinbox.setToolTip(self.tr("Positive = subtitles later, Negative = subtitles earlier"))
        self.offset_spinbox.valueChanged.connect(self._on_offset_changed)
        controls.addWidget(self.offset_spinbox)

        self.compare_button = ModernButton(self.tr("Compare original (A)"), variant="secondary")
        self.compare_button.setCheckable(True)
        self.compare_button.setToolTip(
            self.tr("Play the selected line at its original timing, to hear the difference.")
        )
        self.compare_button.toggled.connect(self._on_compare_toggled)
        controls.addWidget(self.compare_button)

        controls.addStretch()

        # Apply is this dialog's one task action; Cancel is quiet beside it.
        self.apply_button = ModernButton(self.tr("Apply Offset"), variant="primary")
        self.apply_button.clicked.connect(self.accept)
        controls.addWidget(self.apply_button)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self.reject)
        controls.addWidget(self.cancel_button)

        return controls

    def _populate_lines(self) -> None:
        """Fill the line list from the already-parsed entries."""
        for start, _end, text in self._entries:
            # One row is one line: a wrapped cue flattened, never re-wrapped.
            flattened = " ".join(text.split())
            item = QListWidgetItem(f"{_timestamp(start)}  {flattened}")
            item.setData(Qt.ItemDataRole.UserRole, start)
            item.setToolTip(text)
            self.line_list.addItem(item)

    def _disown_default_buttons(self) -> None:
        """Declare this dialog's default button explicitly: it has none.

        Qt promotes the first auto-default push button in a dialog, so a bare
        Return would fire whichever button happened to be built first — from the
        offset field, where Return is how a Japanese input method commits a
        composition (D49). Apply is Ctrl+Return everywhere and bare Return on the
        workbench only, where no text field can hold focus.
        """
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _setup_shortcuts(self) -> None:
        """Bind the transport keys to the workbench, and Apply to the dialog."""
        for key, slot in (
            (QKeySequence("Space"), self.player_widget.toggle_play_pause),
            (QKeySequence("Left"), lambda: self.nudge_offset(-NUDGE_SECONDS)),
            (QKeySequence("Right"), lambda: self.nudge_offset(NUDGE_SECONDS)),
            (QKeySequence("A"), self.compare_button.toggle),
            # Bare Return is safe here and only here: the workbench holds no
            # text field, so it cannot collide with an input method.
            (QKeySequence("Return"), self.accept),
            (QKeySequence("Enter"), self.accept),
        ):
            scoped_shortcut(self.workbench, key, slot)

        # And from anywhere in the dialog, including the offset field.
        primary_action_shortcut(self, self.accept)

    # ------------------------------------------------------------------
    # Offset state
    # ------------------------------------------------------------------

    @property
    def _preview_offset(self) -> float:
        """The offset the player is rendering right now.

        Comparing swaps this to the original; it never touches what Apply keeps.
        """
        return self._initial_offset if self._comparing else self._working_offset

    def get_offset(self) -> float:
        """The offset Apply would commit, in seconds."""
        return self._working_offset

    def nudge_offset(self, delta: float) -> None:
        """Move the working offset by ``delta`` and immediately replay it.

        The point of the whole screen: the change is applied, the selected line
        is re-seeked at the new value, and playback starts, so the correction is
        heard rather than reasoned about.
        """
        target = min(SUBTITLE_OFFSET_MAX, max(SUBTITLE_OFFSET_MIN, self._working_offset + delta))
        self._working_offset = round(target, _OFFSET_DECIMALS)
        self._set_comparing(False)
        self._apply_preview(seek=True, play=True)

    def _set_comparing(self, comparing: bool) -> None:
        """Set the A/B state and keep the button in step without re-entering."""
        self._comparing = comparing
        blocked = self.compare_button.blockSignals(True)
        self.compare_button.setChecked(comparing)
        self.compare_button.blockSignals(blocked)

    def _apply_preview(self, *, seek: bool, play: bool) -> None:
        """Push the preview offset to the player and optionally replay."""
        preview = self._preview_offset
        self.player_widget.set_offset(preview)
        self._sync_readouts()
        if seek:
            start = self._selected_line_start()
            if start is not None:
                self.player_widget.seek_seconds(start + preview)
        if play:
            self.player_widget.play()

    def _sync_readouts(self) -> None:
        """Refresh the overlay and the offset field from the current state."""
        value = f"{self._preview_offset:+.{_OFFSET_DECIMALS}f}"
        if self._comparing:
            self.offset_overlay.setText(tr_format(self.tr("Original %1 s"), value))
        else:
            self.offset_overlay.setText(tr_format(self.tr("Offset %1 s"), value))

        self._echoing_offset = True
        try:
            self.offset_spinbox.setValue(self._working_offset)
        finally:
            self._echoing_offset = False

    def _on_offset_changed(self, value: float) -> None:
        """Adopt a typed offset. Typing sets the value; it does not replay."""
        if self._echoing_offset:
            return
        self._working_offset = value
        self._set_comparing(False)
        self._apply_preview(seek=False, play=False)

    def _on_compare_toggled(self, checked: bool) -> None:
        """Swap between the original and the adjusted timing, and replay."""
        self._comparing = checked
        self._apply_preview(seek=True, play=True)

    # ------------------------------------------------------------------
    # Line selection
    # ------------------------------------------------------------------

    def _selected_line_start(self) -> float | None:
        """Raw start time of the selected line, or None if nothing is selected."""
        item = self.line_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return None if value is None else float(value)

    def _select_initial_line(self) -> None:
        """Park playback on the first line without starting it.

        Selected silently: the dialog opens paused, and a window that starts
        talking the moment it appears is a bad neighbour.
        """
        if not self._entries:
            return
        blocked = self.line_list.blockSignals(True)
        self.line_list.setCurrentRow(0)
        self.line_list.blockSignals(blocked)
        self.player_widget.seek_seconds(self._entries[0][0] + self._preview_offset)

    def _on_line_selected(self, row: int) -> None:
        """Jump to the picked line and play it."""
        if row < 0:
            return
        self._apply_preview(seek=True, play=True)

    # ------------------------------------------------------------------
    # Loading and failure states
    # ------------------------------------------------------------------

    def _show_loading_state(self) -> None:
        """Say that the picture is on its way — or say nothing, honestly."""
        if not self.player_widget.video_surface_available:
            # The player widget already explains why there is no picture, in
            # platform-aware words. Don't narrate it twice.
            #
            # The NARROWER property on purpose: backend_available stays True when
            # only the GL surface is suppressed (preview off by setting or env),
            # so gating on it promised "Loading video…" over a pane that had just
            # said the preview was turned off.
            self.status_label.setText("")
            return
        self.status_label.setText(self.tr("Loading video…"))

    def _on_source_loaded(self) -> None:
        """The file is open: hand the status line over to the key hints."""
        self.status_label.setText(self._hint_text)
        self.clear_screen_issue()

    def _on_playback_failed(self, reason: str) -> None:
        """Say the video failed, and that the offset is still usable."""
        logger.warning("Timing viewer playback failed: %s", reason)
        self.status_label.setText("")
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr("This video could not be played. The offset can still be set by hand."),
                details=reason,
            )
        )

    # ------------------------------------------------------------------
    # Exits
    # ------------------------------------------------------------------

    def _on_align_clicked(self) -> None:
        """Close with the align result so the caller can open the Retime tool.

        This screen does not align anything: it closes, and only once ``exec()``
        has returned does the caller navigate. The mpv core is down by then —
        :meth:`done` releases it before the dialog goes away.
        """
        self.done(self.ALIGN_REQUESTED)

    def done(self, result: int) -> None:
        """Release the mpv core, then close with ``result``.

        Every terminal path — Apply, Cancel, Escape, the window close button and
        the align hand-off — funnels through ``QDialog.done``, which makes this
        the one place the release has to happen. The teardown itself is
        idempotent, so the belt-and-braces ``closeEvent`` call costs nothing.
        """
        self.player_widget.release()
        super().done(result)

    def closeEvent(self, event) -> None:
        """Release the media player on close (joins any in-flight probe)."""
        self.player_widget.release()
        super().closeEvent(event)

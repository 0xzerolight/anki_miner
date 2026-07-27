"""Filterable activity console for long, unattended mining runs.

The widget keeps a bounded store of semantic entries (timestamp, level,
message) and renders the subset matching the active level chip and search
term into the public ``text_edit``.

Two deliberate properties:

* **Severity lives in the text, not in a colour.** The old widget painted
  each line with a hard-coded hex foreground, which vanished the moment a
  user copied the log out and was unreadable in several of the shipped
  themes. ``[ERROR] …`` survives both.
* **Following the tail is conditional.** Auto-scroll happens only while the
  reader is already at the bottom and has not paused it; otherwise new lines
  are counted into the "Jump to latest" affordance so scrolling up to read
  never yanks the viewport back down.

``text_edit`` is a public surface: many tabs and tests read
``log_widget.text_edit.toPlainText()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.constants import LOG_MAX_LINES, LOG_ROTATION_THRESHOLD, MIN_HEIGHT_LOG_WIDGET
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.fonts import make_scaled_monospace_font
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.utils.i18n import tr_format

LEVEL_INFO = "INFO"
LEVEL_SUCCESS = "SUCCESS"
LEVEL_WARNING = "WARNING"
LEVEL_ERROR = "ERROR"

# Level chips. ``None`` means "no restriction"; Info deliberately includes
# SUCCESS so the chip means "everything that went to plan".
FILTER_LEVELS: dict[str, frozenset[str] | None] = {
    "all": None,
    "info": frozenset({LEVEL_INFO, LEVEL_SUCCESS}),
    "warning": frozenset({LEVEL_WARNING}),
    "error": frozenset({LEVEL_ERROR}),
}

# Slack in scrollbar units for "the reader is at the bottom". A wheel notch
# can leave the bar a pixel short of its maximum.
_BOTTOM_SLACK = 2


@dataclass(frozen=True)
class LogEntry:
    """One retained log line."""

    timestamp: str
    level: str
    message: str

    def render(self) -> str:
        """Render as the semantic line shown, copied and saved."""
        return f"[{self.timestamp}] [{self.level}] {self.message}"


class LogWidget(QWidget):
    """Activity console with level chips, search, follow control and export.

    Signals:
        problem_logged: ``(level, message)`` for WARNING and ERROR entries, so
            a container can surface the Activity panel when a run goes wrong.
    """

    problem_logged = pyqtSignal(str, str)

    # Rotation: exceeding MAX_LINES retained entries drops the oldest until
    # only KEEP_LINES remain, so len(entries) <= MAX_LINES always holds.
    MAX_LINES = LOG_MAX_LINES
    KEEP_LINES = LOG_ROTATION_THRESHOLD

    def __init__(self, parent=None):
        """Initialize the log widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._entries: list[LogEntry] = []
        self._level_filter = "all"
        self._search = ""
        self._follow_paused = False
        self._pending_new = 0
        self._setup_ui()
        self._update_match_label()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Set minimum height to prevent collapsing
        self.setMinimumHeight(MIN_HEIGHT_LOG_WIDGET)

        header = QWidget()
        header.setObjectName("log-header")
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)
        header_layout.setSpacing(SPACING.xs)
        header_layout.addLayout(self._build_title_row())
        header_layout.addLayout(self._build_filter_row())
        header.setLayout(header_layout)
        layout.addWidget(header)

        # Text edit for log content
        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("log-widget")
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        # Machine output: the platform's own fixed-width face, at the text size
        # the user chose. It used to ask for 'Consolas' at a constant 13px —
        # a Windows-only family that no other desktop has, at a size that
        # ignored the text-size setting entirely (decision D44-B).
        self.text_edit.setFont(make_scaled_monospace_font(FONT_SIZES.body_sm))

        scrollbar = self.text_edit.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.valueChanged.connect(self._on_scrolled)

        layout.addWidget(self.text_edit)

        # Catch-up affordance, shown only while lines arrive off-screen.
        self.jump_button = self._make_button("", "ghost")
        self.jump_button.setToolTip(self.tr("Scroll to the newest line and resume following it."))
        self.jump_button.clicked.connect(self._on_jump_clicked)
        self.jump_button.hide()
        layout.addWidget(self.jump_button)

        self.setLayout(layout)

        # Apply card styling
        self.setObjectName("card")

        # Set size policy to allow expansion
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _build_title_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        title_label = QLabel(self.tr("Activity Log"))
        title_font = QFont()
        title_font.setWeight(QFont.Weight.Bold)
        title_label.setFont(title_font)
        row.addWidget(title_label)
        row.addStretch()

        self.copy_visible_button = self._make_button(self.tr("Copy visible"), "ghost")
        self.copy_visible_button.setToolTip(self.tr("Copy only the lines currently shown."))
        self.copy_visible_button.clicked.connect(self._on_copy_visible_clicked)
        row.addWidget(self.copy_visible_button)

        self.copy_all_button = self._make_button(self.tr("Copy all"), "ghost")
        self.copy_all_button.setToolTip(self.tr("Copy every retained line, ignoring the filters."))
        self.copy_all_button.clicked.connect(self._on_copy_all_clicked)
        row.addWidget(self.copy_all_button)

        self.save_button = self._make_button(self.tr("Save run log…"), "ghost")
        self.save_button.setToolTip(self.tr("Write every retained line to a UTF-8 text file."))
        self.save_button.clicked.connect(self._on_save_clicked)
        row.addWidget(self.save_button)

        self.clear_button = self._make_button(self.tr("Clear"), "ghost")
        self.clear_button.setToolTip(self.tr("Clear all log messages"))
        self.clear_button.clicked.connect(self._on_clear_clicked)
        row.addWidget(self.clear_button)

        return row

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        labels = {
            "all": self.tr("All"),
            "info": self.tr("Info"),
            "warning": self.tr("Warnings"),
            "error": self.tr("Errors"),
        }
        tooltips = {
            "all": self.tr("Show every line."),
            "info": self.tr("Show progress and success lines."),
            "warning": self.tr("Show warnings only."),
            "error": self.tr("Show errors only."),
        }
        self.filter_buttons: dict[str, QPushButton] = {}
        for key in FILTER_LEVELS:
            button = self._make_button(labels[key], "ghost")
            button.setCheckable(True)
            button.setToolTip(tooltips[key])
            button.clicked.connect(lambda _checked, k=key: self.set_level_filter(k))
            self.filter_buttons[key] = button
            row.addWidget(button)
        self._sync_filter_buttons()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Search…"))
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        row.addWidget(self.search_input, 1)

        self.match_label = QLabel()
        self.match_label.setToolTip(self.tr("Lines shown of lines retained."))
        row.addWidget(self.match_label)

        self.pause_button = self._make_button(self.tr("Pause follow"), "ghost")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip(self.tr("Stop scrolling to the newest line while you read."))
        self.pause_button.toggled.connect(self._on_pause_toggled)
        row.addWidget(self.pause_button)

        return row

    def _make_button(self, text: str, variant: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(variant)
        return button

    # -------------------------------------------------------------- Public

    def append_info(self, message: str) -> None:
        """Append an info message.

        Args:
            message: Message to append
        """
        self._append_message(message, LEVEL_INFO)

    def append_success(self, message: str) -> None:
        """Append a success message.

        Args:
            message: Message to append
        """
        self._append_message(message, LEVEL_SUCCESS)

    def append_warning(self, message: str) -> None:
        """Append a warning message.

        Args:
            message: Message to append
        """
        self._append_message(message, LEVEL_WARNING)

    def append_error(self, message: str) -> None:
        """Append an error message.

        Args:
            message: Message to append
        """
        self._append_message(message, LEVEL_ERROR)

    def clear_log(self) -> None:
        """Clear all log messages."""
        self._entries.clear()
        self._pending_new = 0
        self.text_edit.clear()
        self._update_jump_button()
        self._update_match_label()

    def set_level_filter(self, key: str) -> None:
        """Show only the levels covered by chip ``key`` (see ``FILTER_LEVELS``)."""
        if key not in FILTER_LEVELS:
            return
        self._level_filter = key
        self._sync_filter_buttons()
        self._render_all()

    def visible_text(self) -> str:
        """Return the text currently rendered, filters applied."""
        return self.text_edit.toPlainText()

    def full_text(self) -> str:
        """Return every retained entry, filters ignored."""
        return self._render_entries(self._entries)

    # ------------------------------------------------------------ Internals

    def _append_message(self, text: str, level: str) -> None:
        """Store a message and render it if it passes the active filters.

        Args:
            text: Message text
            level: One of ``INFO``, ``SUCCESS``, ``WARNING``, ``ERROR``
        """
        entry = LogEntry(datetime.now().strftime("%H:%M:%S"), level, text)
        self._entries.append(entry)

        # "At the bottom" has to be sampled before the insert: appending text
        # grows the scrollbar's range, so a reader who was at the bottom would
        # otherwise look scrolled up the instant the line lands.
        was_at_bottom = self._is_at_bottom()
        rendered = self._matches(entry)

        if len(self._entries) > self.MAX_LINES:
            del self._entries[: len(self._entries) - self.KEEP_LINES]
            self._render_all(keep_position=True)
        elif rendered:
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(entry.render() + "\n")

        self._update_match_label()

        # A line the filters hide changes nothing on screen, so it neither
        # scrolls the view nor counts towards "N new".
        if rendered:
            if was_at_bottom and not self._follow_paused:
                self._scroll_to_bottom()
            else:
                self._pending_new += 1
                self._update_jump_button()

        if level in (LEVEL_WARNING, LEVEL_ERROR):
            self.problem_logged.emit(level, text)

    def _matches(self, entry: LogEntry) -> bool:
        levels = FILTER_LEVELS[self._level_filter]
        if levels is not None and entry.level not in levels:
            return False
        return not self._search or self._search in entry.render().casefold()

    def _render_entries(self, entries: list[LogEntry]) -> str:
        return "".join(entry.render() + "\n" for entry in entries)

    def _render_all(self, *, keep_position: bool = False) -> None:
        scrollbar = self.text_edit.verticalScrollBar()
        position = scrollbar.value() if (keep_position and scrollbar is not None) else None

        self.text_edit.setPlainText(self._render_entries([e for e in self._entries if self._matches(e)]))
        self._update_match_label()

        if position is not None and scrollbar is not None:
            scrollbar.setValue(min(position, scrollbar.maximum()))
        else:
            # Changing a chip or the search term is an explicit request to see
            # the matches, so it lands on the newest one even while paused —
            # pause only suppresses the involuntary pull of an incoming line.
            self._scroll_to_bottom()

    def _update_match_label(self) -> None:
        shown = sum(1 for entry in self._entries if self._matches(entry))
        self.match_label.setText(tr_format(self.tr("%1 of %2"), shown, len(self._entries)))

    def _sync_filter_buttons(self) -> None:
        # Checked is the whole signal: the shared `:checked` rule paints it (D41).
        # These chips must not restyle themselves as the screen's primary action.
        for key, button in self.filter_buttons.items():
            button.setChecked(key == self._level_filter)

    def _on_search_changed(self, text: str) -> None:
        self._search = text.strip().casefold()
        self._render_all()

    # ---------------------------------------------------------------- Follow

    def _is_at_bottom(self) -> bool:
        scrollbar = self.text_edit.verticalScrollBar()
        if scrollbar is None:
            return True
        return scrollbar.value() >= scrollbar.maximum() - _BOTTOM_SLACK

    def _scroll_to_bottom(self) -> None:
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()
        scrollbar = self.text_edit.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())
        self._pending_new = 0
        self._update_jump_button()

    def _update_jump_button(self) -> None:
        if self._pending_new <= 0:
            self.jump_button.hide()
            return
        self.jump_button.setText(tr_format(self.tr("↓ %1 new — Jump to latest"), self._pending_new))
        self.jump_button.show()

    def _on_scrolled(self, _value: int) -> None:
        """Reaching the bottom by hand retires the catch-up affordance."""
        if self._pending_new and self._is_at_bottom():
            self._pending_new = 0
            self._update_jump_button()

    def _on_jump_clicked(self) -> None:
        self._scroll_to_bottom()

    def _on_pause_toggled(self, checked: bool) -> None:
        self._follow_paused = checked
        if not checked:
            self._scroll_to_bottom()

    # ------------------------------------------------------------ Copy/save

    def _on_copy_visible_clicked(self) -> None:
        self._copy(self.visible_text(), self.copy_visible_button, self.tr("Copy visible"))

    def _on_copy_all_clicked(self) -> None:
        self._copy(self.full_text(), self.copy_all_button, self.tr("Copy all"))

    def _copy(self, text: str, button: QPushButton, label: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(text)
        button.setText(self.tr("Copied!"))
        # Parented to the button rather than QTimer.singleShot: a log widget
        # torn down inside its window takes the pending timer with it, so the
        # restore never fires against a deleted C++ object.
        timer = QTimer(button)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: button.setText(label))
        timer.timeout.connect(timer.deleteLater)
        timer.start(2000)

    def _on_save_clicked(self) -> None:
        default_name = f"anki-miner-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        path_str, _ = file_dialogs.get_save_file_name(
            self,
            self.tr("Save Run Log"),
            str(Path(resolve_start_dir(None, file_mode=True)) / default_name),
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return

        target = Path(path_str)
        text = self.full_text()

        def work() -> object:
            target.write_text(text, encoding="utf-8")
            return str(target)

        self.save_button.setEnabled(False)
        run_off_thread(
            self,
            work,
            self._on_save_done,
            self._on_save_error,
            on_finished=self._on_save_finished,
        )

    def _on_save_done(self, result: object) -> None:
        self.append_info(tr_format(self.tr("Saved run log to %1"), result))

    def _on_save_error(self, message: str) -> None:
        self.append_error(tr_format(self.tr("Could not save the run log: %1"), message))

    def _on_save_finished(self) -> None:
        self.save_button.setEnabled(True)

    def _on_clear_clicked(self) -> None:
        """Handle clear button click."""
        self.clear_log()

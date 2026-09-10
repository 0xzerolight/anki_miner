"""Entry picker shown after the Download tool resolves a playlist URL.

Playlist support in this tool is *expansion*, not a download mode: the chosen
entries become ordinary URL lines in the tab's box, so each one downloads
through the existing per-item progress, cancel and already-present-skip path.
This dialog is where the user says which ones.

The range field mirrors yt-dlp's ``--playlist-items`` syntax, which is the
gesture that makes "the first twenty of five hundred" one action rather than
twenty clicks.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.playlist_selection import SelectionError, parse_index_selection
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.services.media_downloader import DownloadPlaylist
from anki_miner.utils.i18n import tr_format


def _format_duration(seconds: int | None) -> str:
    """Render *seconds* as M:SS, or H:MM:SS past an hour. Blank when unknown."""
    if seconds is None:
        return ""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class PlaylistPickerDialog(QDialog):
    """Choose which playlist entries to add to the download queue.

    Args:
        playlist: The resolved playlist.
        truncated: Whether the probe hit its cap, so entries beyond it exist.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        playlist: DownloadPlaylist,
        *,
        truncated: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Add from Playlist"))
        self.setMinimumSize(560, 480)
        self._playlist = playlist

        layout = QVBoxLayout(self)

        entries = playlist.entries
        self._first_index = entries[0].index if entries else 1
        last = entries[-1].index if entries else 0
        total = playlist.total_count
        if total is None and not truncated:
            total = last
        if total is not None:
            header = tr_format(
                self.tr("'%1' — showing videos %2-%3 of %4"),
                playlist.title,
                str(self._first_index),
                str(last),
                str(total),
            )
        else:
            header = tr_format(
                self.tr("'%1' — showing videos %2-%3"),
                playlist.title,
                str(self._first_index),
                str(last),
            )
        self.header_label = QLabel(header)
        self.header_label.setWordWrap(True)
        layout.addWidget(self.header_label)

        self.truncation_label = QLabel(
            self.tr("This playlist has more videos. Paste the URL again to continue from here.")
        )
        self.truncation_label.setObjectName("helper-text")
        self.truncation_label.setWordWrap(True)
        self.truncation_label.setHidden(not truncated)
        layout.addWidget(self.truncation_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("Search this playlist…"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_search)
        layout.addWidget(self.search_edit)

        self.entry_list = QListWidget()
        self.entry_list.itemChanged.connect(self._refresh_add_button)
        layout.addWidget(self.entry_list, 1)
        for entry in playlist.entries:
            label = f"{entry.index}. {entry.title}   {_format_duration(entry.duration_s)}".rstrip()
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.url)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.entry_list.addItem(item)

        controls = QHBoxLayout()
        self.select_all_button = QPushButton(self.tr("All"))
        self.select_all_button.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        controls.addWidget(self.select_all_button)

        self.select_none_button = QPushButton(self.tr("None"))
        self.select_none_button.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        controls.addWidget(self.select_none_button)

        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText(self.tr("Range, e.g. 1-20,25"))
        self.range_edit.returnPressed.connect(self._apply_range)
        controls.addWidget(self.range_edit, 1)

        self.apply_range_button = QPushButton(self.tr("Select Range"))
        self.apply_range_button.clicked.connect(self._apply_range)
        controls.addWidget(self.apply_range_button)
        layout.addLayout(controls)

        self.range_error_label = QLabel()
        self.range_error_label.setObjectName("helper-text")
        self.range_error_label.setWordWrap(True)
        self.range_error_label.setHidden(True)
        layout.addWidget(self.range_error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = QPushButton(self.tr("Add"))
        buttons.addButton(self.add_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_add_button()

        add_min_max_buttons(self)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _apply_search(self, text: str) -> None:
        """Hide rows not matching *text*. Hidden rows keep their tick state."""
        needle = text.strip().lower()
        for row in range(self.entry_list.count()):
            item = self.entry_list.item(row)
            assert item is not None
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _set_all(self, state: Qt.CheckState) -> None:
        """Tick or untick every row in one pass.

        Signals are blocked across the loop: a per-item itemChanged storm over a
        500-row list is visibly slow, and one refresh at the end is equivalent.
        """
        self.entry_list.blockSignals(True)
        try:
            for row in range(self.entry_list.count()):
                item = self.entry_list.item(row)
                assert item is not None
                item.setCheckState(state)
        finally:
            self.entry_list.blockSignals(False)
        self._refresh_add_button()

    def _apply_range(self) -> None:
        """Replace the selection with the rows the range expression names.

        Replacing rather than adding is what makes "1-20" on a long playlist one
        gesture. A malformed expression reports and changes nothing.
        """
        try:
            wanted = parse_index_selection(self.range_edit.text(), len(self._playlist.entries), first=self._first_index)
        except SelectionError as exc:
            self.range_error_label.setText(self._range_error_text(exc))
            self.range_error_label.setHidden(False)
            return
        self.range_error_label.setHidden(True)
        self.entry_list.blockSignals(True)
        try:
            for row in range(self.entry_list.count()):
                item = self.entry_list.item(row)
                assert item is not None
                state = Qt.CheckState.Checked if (self._first_index + row) in wanted else Qt.CheckState.Unchecked
                item.setCheckState(state)
        finally:
            self.entry_list.blockSignals(False)
        self._refresh_add_button()

    def _range_error_text(self, exc: SelectionError) -> str:
        """The translated sentence for a malformed range part."""
        messages = {
            "not_a_range": self.tr("Not a number or a range: %1"),
            "no_such_video": self.tr("Video %1 is not on this page."),
            "open_range": self.tr("A range needs at least one end."),
            "from_one": self.tr("Videos are numbered from 1."),
        }
        return tr_format(messages.get(exc.kind, messages["not_a_range"]), exc.value)

    def _refresh_add_button(self, *_: object) -> None:
        """Name the count on the button, and disable it at zero."""
        count = len(self.selected_urls())
        template = self.tr("Add %1 video") if count == 1 else self.tr("Add %1 videos")
        self.add_button.setText(tr_format(template, str(count)))
        self.add_button.setEnabled(count > 0)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def selected_urls(self) -> list[str]:
        """Ticked entry URLs in playlist order, including search-hidden rows."""
        urls: list[str] = []
        for row in range(self.entry_list.count()):
            item = self.entry_list.item(row)
            assert item is not None
            if item.checkState() == Qt.CheckState.Checked:
                urls.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return urls

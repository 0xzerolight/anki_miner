"""Dialog for curating words before card creation."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.models import TokenizedWord


class _NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric key instead of display text.

    Avoids the default lexicographic sort that places "100" before "20".
    Missing values use ``inf`` so unranked rows cluster at one end.
    """

    _SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, text: str, sort_key: float) -> None:
        super().__init__(text)
        self.setData(self._SORT_ROLE, sort_key)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        own = self.data(self._SORT_ROLE)
        theirs = other.data(self._SORT_ROLE)
        if own is None or theirs is None:
            return super().__lt__(other)
        return float(own) < float(theirs)


class WordCurationDialog(QDialog):
    """Dialog for selecting which words to include in card creation.

    Shows a table of words with checkboxes. Users can search/filter,
    select/deselect all, and confirm their selection.
    """

    def __init__(
        self,
        words: list[TokenizedWord],
        parent=None,
        mark_known_callback: Callable[[set[str]], int] | None = None,
    ):
        super().__init__(parent)
        self._words = words
        # Callback invoked with the set of mined forms when the user adds rows to
        # the local known/ignore list (Issue #42). Persisted immediately so the
        # words stick even if the dialog is later cancelled.
        self._mark_known_callback = mark_known_callback
        # Mined forms the user marked as known this session (exposed for tests).
        self._marked_known: set[str] = set()
        self._setup_ui()
        self._populate_table()
        self._update_word_count()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Word Curation")
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        self.resize(1100, 700)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header
        header = QLabel("Select words for card creation")
        header.setFont(self._make_font(16, QFont.Weight.Bold))
        layout.addWidget(header)

        # Controls row
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(SPACING.sm)

        # Search bar
        search_label = QLabel("Search:")
        controls_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by any field...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.setMinimumWidth(200)
        controls_layout.addWidget(self.search_input)

        controls_layout.addSpacing(16)

        # Select All / Deselect All
        _bulk_tooltip = (
            "Acts on highlighted rows when 2 or more are selected "
            "(Ctrl+Click or Shift+Click to select). Otherwise acts on all visible rows."
        )
        self.select_all_button = ModernButton("Select All", variant="secondary")
        self.select_all_button.clicked.connect(self._select_all)
        self.select_all_button.setToolTip(_bulk_tooltip)
        controls_layout.addWidget(self.select_all_button)

        self.deselect_all_button = ModernButton("Deselect All", variant="secondary")
        self.deselect_all_button.clicked.connect(self._deselect_all)
        self.deselect_all_button.setToolTip(_bulk_tooltip)
        controls_layout.addWidget(self.deselect_all_button)

        # Add to local known/ignore list (Issue #42). Acts on the highlighted
        # rows, or the current row when nothing is highlighted — deliberately NOT
        # all visible rows, to avoid ignoring the whole list by accident.
        self.add_known_button = ModernButton("Add to Known Words", variant="secondary")
        self.add_known_button.clicked.connect(self._on_add_to_known)
        self.add_known_button.setToolTip(
            "Permanently ignore the highlighted row(s) — adds them to your local "
            "Known Words list so they are never mined again. Falls back to the "
            "current row when none are highlighted."
        )
        controls_layout.addWidget(self.add_known_button)

        controls_layout.addStretch()

        # Word count label
        self.word_count_label = QLabel()
        self.word_count_label.setFont(self._make_font(12, QFont.Weight.Medium))
        controls_layout.addWidget(self.word_count_label)

        layout.addLayout(controls_layout)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["", "Word (mined)", "Form in subtitle", "Reading", "Sentence", "Freq. Rank"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)

        header_view = self.table.horizontalHeader()
        if header_view:
            header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            header_view.resizeSection(0, 40)
            header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        v_header = self.table.verticalHeader()
        if v_header:
            v_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        self.table.itemChanged.connect(self._on_item_changed)

        layout.addWidget(self.table)

        # Footer buttons
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()

        cancel_button = ModernButton("Cancel", variant="secondary")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setMinimumWidth(100)
        footer_layout.addWidget(cancel_button)

        confirm_button = ModernButton("Confirm Selection", variant="primary")
        confirm_button.clicked.connect(self.accept)
        confirm_button.setMinimumWidth(140)
        footer_layout.addWidget(confirm_button)

        layout.addLayout(footer_layout)

        self.setLayout(layout)
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts for word curation."""
        # Space: Toggle checkbox of selected rows (or current row if none selected)
        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.table)
        space_shortcut.activated.connect(self._toggle_selected_rows)

        # Ctrl+A: Select all words (scoped to table so it doesn't override text selection in search)
        select_all_shortcut = QShortcut(QKeySequence("Ctrl+A"), self.table)
        select_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        select_all_shortcut.activated.connect(self._select_all)

        # Ctrl+D: Deselect all words (scoped to table)
        deselect_all_shortcut = QShortcut(QKeySequence("Ctrl+D"), self.table)
        deselect_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        deselect_all_shortcut.activated.connect(self._deselect_all)

        # Enter/Return: Confirm selection
        enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self.table)
        enter_shortcut.activated.connect(self.accept)

    def _make_font(self, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        font = QFont()
        font.setPixelSize(size)
        font.setWeight(weight)
        return font

    def _populate_table(self) -> None:
        """Fill the table with words, all checked by default."""
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._words))

        for row, word in enumerate(self._words):
            # Checkbox column
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Checked)
            check_item.setData(Qt.ItemDataRole.UserRole, row)  # Store original index
            self.table.setItem(row, 0, check_item)

            # Word (mined) — what becomes the Anki Expression
            # (lemma for verbs/adjectives, surface for nouns)
            self.table.setItem(row, 1, self._make_readonly_item(word.mined_form))

            # Form in subtitle — the raw surface as it appeared
            self.table.setItem(row, 2, self._make_readonly_item(word.surface))

            # Reading
            self.table.setItem(row, 3, self._make_readonly_item(word.reading))

            # Sentence (truncated)
            sentence = word.sentence
            display = sentence if len(sentence) <= 50 else sentence[:47] + "..."
            item = self._make_readonly_item(display)
            item.setToolTip(sentence)
            self.table.setItem(row, 4, item)

            # Frequency Rank — sort numerically, not lexically (issue #6)
            if word.frequency_rank is not None:
                rank_item = _NumericTableWidgetItem(str(word.frequency_rank), float(word.frequency_rank))
            else:
                rank_item = _NumericTableWidgetItem("-", float("inf"))
            rank_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 5, rank_item)

        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)

    def _make_readonly_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Called when any table item changes (e.g. checkbox toggled)."""
        if item.column() == 0:
            self._update_word_count()

    def _on_search_changed(self, text: str) -> None:
        """Filter visible rows based on search text."""
        text_lower = text.lower()
        for row in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(row, False)
                continue

            # Check surface, lemma, reading, sentence columns
            visible = False
            for col in (1, 2, 3, 4):
                cell = self.table.item(row, col)
                if cell and text_lower in cell.text().lower():
                    visible = True
                    break
            self.table.setRowHidden(row, not visible)

    def _target_rows(self) -> list[int]:
        """Return rows for bulk actions: highlighted rows if 2+, else all visible.

        Uses the QTableWidget multi-row selection (Ctrl/Shift+Click) when the
        user has selected at least two rows. Falls back to every visible row so
        legacy single-click + Select All behaviour is preserved.
        """
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selected = sorted(
                {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
            )
            if len(selected) >= 2:
                return selected
        return [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]

    def _select_all(self) -> None:
        """Check rows in the current bulk-action target set."""
        self.table.blockSignals(True)
        for row in self._target_rows():
            item = self.table.item(row, 0)
            if item and self._is_checkable(item):
                item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._update_word_count()

    def _deselect_all(self) -> None:
        """Uncheck rows in the current bulk-action target set."""
        self.table.blockSignals(True)
        for row in self._target_rows():
            item = self.table.item(row, 0)
            if item and self._is_checkable(item):
                item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_word_count()

    @staticmethod
    def _is_checkable(item: QTableWidgetItem) -> bool:
        """Whether a checkbox item still accepts toggling.

        Rows added to the known/ignore list have their checkable flag stripped so
        bulk actions and Space can't re-include them (Issue #42).
        """
        return bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def _toggle_selected_rows(self) -> None:
        """Toggle checkboxes for highlighted rows, or the current row when none.

        If any target row is unchecked, all flip to Checked; otherwise all flip
        to Unchecked. Falls back to the focused row when the selection is empty
        so Space on a single-cursor view still toggles that one row.
        """
        selection_model = self.table.selectionModel()
        rows: list[int] = []
        if selection_model is not None:
            rows = sorted(
                {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
            )
        if not rows:
            current = self.table.currentRow()
            if current < 0 or self.table.isRowHidden(current):
                return
            rows = [current]

        items = [item for row in rows if (item := self.table.item(row, 0)) is not None and self._is_checkable(item)]
        if not items:
            return
        any_unchecked = any(item.checkState() != Qt.CheckState.Checked for item in items)
        new_state = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked

        self.table.blockSignals(True)
        for item in items:
            item.setCheckState(new_state)
        self.table.blockSignals(False)
        self._update_word_count()

    _toggle_current_row = _toggle_selected_rows

    def _known_target_rows(self) -> list[int]:
        """Rows for "Add to Known Words": highlighted rows, else the current row.

        Unlike :meth:`_target_rows`, this never falls back to every visible row —
        ignoring an entire filtered list with one click would be too easy to
        trigger by accident.
        """
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selected = sorted(
                {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
            )
            if selected:
                return selected
        current = self.table.currentRow()
        if current >= 0 and not self.table.isRowHidden(current):
            return [current]
        return []

    def _on_add_to_known(self) -> None:
        """Add the target rows to the local known/ignore list (Issue #42).

        Persists immediately via the callback, then strikes through and unchecks
        the rows so they are excluded from this run and can't be re-checked.
        """
        rows = [row for row in self._known_target_rows() if self._row_is_active(row)]
        if not rows:
            return

        forms: set[str] = set()
        for row in rows:
            word_item = self.table.item(row, 1)  # "Word (mined)" column
            if word_item:
                forms.add(word_item.text())
        if not forms:
            return

        if self._mark_known_callback is not None:
            self._mark_known_callback(forms)
        self._marked_known |= forms

        self.table.blockSignals(True)
        for row in rows:
            self._mark_row_known(row)
        self.table.blockSignals(False)
        self._update_word_count()

    def _row_is_active(self, row: int) -> bool:
        """Whether a row hasn't already been marked known (checkbox still toggles)."""
        item = self.table.item(row, 0)
        return item is not None and self._is_checkable(item)

    def _mark_row_known(self, row: int) -> None:
        """Visually mark a row as ignored: strikethrough, grey, unchecked, locked."""
        check_item = self.table.item(row, 0)
        if check_item:
            check_item.setCheckState(Qt.CheckState.Unchecked)
            # Strip the checkable flag so bulk actions / Space can't re-include it.
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        grey = QColor(128, 128, 128)
        for col in range(1, self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(grey)

    def _update_word_count(self) -> None:
        """Update the word count label."""
        selected = sum(
            1
            for row in range(self.table.rowCount())
            if (item := self.table.item(row, 0)) and item.checkState() == Qt.CheckState.Checked
        )
        total = len(self._words)
        self.word_count_label.setText(f"{selected} of {total} words selected")

    def get_selected_words(self) -> list[TokenizedWord]:
        """Return the list of checked words."""
        selected = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                original_index = item.data(Qt.ItemDataRole.UserRole)
                if original_index is not None and 0 <= original_index < len(self._words):
                    selected.append(self._words[original_index])
        return selected

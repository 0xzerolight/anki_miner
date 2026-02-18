"""Dialog for curating words before card creation."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
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


class WordCurationDialog(QDialog):
    """Dialog for selecting which words to include in card creation.

    Shows a table of words with checkboxes. Users can search/filter,
    select/deselect all, and confirm their selection.
    """

    def __init__(self, words: list[TokenizedWord], parent=None):
        super().__init__(parent)
        self._words = words
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
        self.select_all_button = ModernButton("Select All", variant="secondary")
        self.select_all_button.clicked.connect(self._select_all)
        controls_layout.addWidget(self.select_all_button)

        self.deselect_all_button = ModernButton("Deselect All", variant="secondary")
        self.deselect_all_button.clicked.connect(self._deselect_all)
        controls_layout.addWidget(self.deselect_all_button)

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
            ["", "Surface", "Lemma", "Reading", "Sentence", "Freq. Rank"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
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
        # Space: Toggle selection of current row (scoped to table)
        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.table)
        space_shortcut.activated.connect(self._toggle_current_row)

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
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Checked)
            check_item.setData(Qt.ItemDataRole.UserRole, row)  # Store original index
            self.table.setItem(row, 0, check_item)

            # Surface
            self.table.setItem(row, 1, self._make_readonly_item(word.surface))

            # Lemma
            self.table.setItem(row, 2, self._make_readonly_item(word.lemma))

            # Reading
            self.table.setItem(row, 3, self._make_readonly_item(word.reading))

            # Sentence (truncated)
            sentence = word.sentence
            display = sentence if len(sentence) <= 50 else sentence[:47] + "..."
            item = self._make_readonly_item(display)
            item.setToolTip(sentence)
            self.table.setItem(row, 4, item)

            # Frequency Rank
            rank_str = str(word.frequency_rank) if word.frequency_rank is not None else "-"
            self.table.setItem(row, 5, self._make_readonly_item(rank_str))

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

    def _select_all(self) -> None:
        """Check all visible rows."""
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, 0)
                if item:
                    item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._update_word_count()

    def _deselect_all(self) -> None:
        """Uncheck all visible rows."""
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, 0)
                if item:
                    item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_word_count()

    def _toggle_current_row(self) -> None:
        """Toggle checkbox of the currently selected row."""
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            item.setCheckState(Qt.CheckState.Unchecked)
        else:
            item.setCheckState(Qt.CheckState.Checked)

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
                selected.append(self._words[original_index])
        return selected

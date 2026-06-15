"""Dialog for managing the local user-curated known/ignore word list (Issue #42).

Shows the words the user added from the Word Curator (``source='user'``), lets
them remove entries, export the list to a plain-text file (one word per line, for
round-tripping back into jiten.moe), and reset it. The Anki-synced cache rows are
not editable here — only counted for context.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.services.known_word_db import KnownWordDB


class KnownWordsManagerDialog(QDialog):
    """View / remove / export / reset the user-curated known words list."""

    def __init__(self, known_word_db: KnownWordDB, parent=None):
        super().__init__(parent)
        self._db = known_word_db
        # The list may never have been written if the user only just enabled the
        # feature — initialize so reads/writes don't hit a missing file.
        self._db.initialize()
        self._setup_ui()
        self._refresh()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Manage Known Words")
        self.setMinimumWidth(480)
        self.setMinimumHeight(520)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        header = QLabel("Local Known Words")
        font = QFont()
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        layout.addWidget(header)

        helper = QLabel(
            "Words you added from the Word Curator. These are ignored on every "
            "mining run, kept when you rebuild the cache, and exportable for "
            "re-import into jiten.moe."
        )
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter…")
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        self.word_list = QListWidget()
        self.word_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.word_list)

        self.count_label = QLabel()
        self.count_label.setObjectName("helper-text")
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.remove_button = ModernButton("Remove Selected", variant="secondary")
        self.remove_button.clicked.connect(self._on_remove)
        self.export_button = ModernButton("Export…", variant="secondary")
        self.export_button.clicked.connect(self._on_export)
        self.reset_button = ModernButton("Reset User List", variant="danger")
        self.reset_button.clicked.connect(self._on_reset)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        close_button = ModernButton("Close", variant="primary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Reload the user words from the DB and update the list + count label."""
        user_words = sorted(self._db.get_words_by_source("user"))
        self.word_list.clear()
        self.word_list.addItems(user_words)
        self._on_search_changed(self.search_input.text())

        cached = max(0, self._db.word_count() - len(user_words))
        self.count_label.setText(f"{len(user_words)} user word(s) · {cached} cached from Anki")

    def _on_search_changed(self, text: str) -> None:
        needle = text.lower()
        for row in range(self.word_list.count()):
            item = self.word_list.item(row)
            if item is not None:
                item.setHidden(bool(needle) and needle not in item.text().lower())

    def _selected_words(self) -> set[str]:
        return {item.text() for item in self.word_list.selectedItems()}

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_remove(self) -> None:
        words = self._selected_words()
        if not words:
            return
        self._db.remove_words(words)
        self._refresh()

    def export_to(self, path: Path) -> int:
        """Write the user words to ``path``, one per line (UTF-8). Returns the count."""
        words = sorted(self._db.get_words_by_source("user"))
        path.write_text("\n".join(words) + ("\n" if words else ""), encoding="utf-8")
        return len(words)

    def _on_export(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Known Words",
            str(Path(resolve_start_dir(None, file_mode=True)) / "known_words.txt"),
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return
        count = self.export_to(Path(path_str))
        QMessageBox.information(self, "Export Complete", f"Exported {count} word(s) to:\n{path_str}")

    def _on_reset(self) -> None:
        if self.word_list.count() == 0:
            return
        reply = QMessageBox.question(
            self,
            "Reset User List",
            "Remove ALL words you added to the local known words list? "
            "This cannot be undone. The Anki-synced cache is not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.clear_user()
            self._refresh()

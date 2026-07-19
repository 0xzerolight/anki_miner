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
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.known_words_import import (
    KnownWordsImportError,
    KnownWordsImportResult,
    parse_known_words_file,
)
from anki_miner.utils.i18n import tr_format


class KnownWordsManagerDialog(QDialog):
    """View / remove / export / reset the user-curated known words list."""

    def __init__(self, known_word_db: KnownWordDB, parent=None):
        super().__init__(parent)
        self._db = known_word_db
        # The list may never have been written if the user only just enabled the
        # feature — initialize so reads/writes don't hit a missing file.
        self._db.initialize()
        self._setup_ui()
        add_min_max_buttons(self)
        self._refresh()

    def _setup_ui(self) -> None:
        self.setWindowTitle(self.tr("Manage Known Words"))
        self.setMinimumWidth(480)
        self.setMinimumHeight(520)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        header = QLabel(self.tr("Local Known Words"))
        font = QFont()
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        layout.addWidget(header)

        helper = QLabel(
            self.tr(
                "Words you added from the Word Curator — ignored on every run, kept "
                "across cache rebuilds, exportable for re-import into jiten.moe. "
                "Import accepts jpdb, Migaku and AnkiMorphs exports or plain word lists."
            )
        )
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Filter…"))
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        self.word_list = QListWidget()
        self.word_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.word_list)

        self.count_label = QLabel()
        self.count_label.setObjectName("helper-text")
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.remove_button = ModernButton(self.tr("Remove Selected"), variant="secondary")
        self.remove_button.clicked.connect(self._on_remove)
        self.import_button = ModernButton(self.tr("Import…"), variant="secondary")
        self.import_button.clicked.connect(self._on_import)
        self.export_button = ModernButton(self.tr("Export…"), variant="secondary")
        self.export_button.clicked.connect(self._on_export)
        self.reset_button = ModernButton(self.tr("Reset User List"), variant="danger")
        self.reset_button.clicked.connect(self._on_reset)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        close_button = ModernButton(self.tr("Close"), variant="primary")
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
        self.count_label.setText(tr_format(self.tr("%1 user word(s) · %2 cached from Anki"), len(user_words), cached))

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

    def _format_display_name(self, format_key: str) -> str:
        """Translated label for a parser format key (keep in lockstep with FORMAT_KEYS)."""
        labels = {
            "jpdb": self.tr("jpdb review export"),
            "migaku_json": self.tr("Migaku word export"),
            "migaku_legacy": self.tr("Migaku legacy add-on backup"),
            "ankimorphs": self.tr("AnkiMorphs known morphs"),
            "migaku_csv": self.tr("Migaku word export (CSV)"),
            "generic": self.tr("plain word list"),
        }
        return labels.get(format_key, format_key)

    def apply_import(self, result: KnownWordsImportResult) -> tuple[int, int]:
        """Insert the parsed words as ``source='user'``; return (added, already).

        "Already in your list" is measured against the prior ``source='user'``
        set, not ``add_words``' row delta — an anki→user upgrade is row-count
        neutral but genuinely new to the user list.
        """
        existing_user = self._db.get_words_by_source("user")
        new_to_list = set(result.words) - existing_user
        self._db.add_words(set(result.words), source="user")
        return len(new_to_list), len(result.words) - len(new_to_list)

    def _on_import(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Import Known Words"),
            resolve_start_dir(None, file_mode=True),
            self.tr("Known word lists (*.csv *.txt *.json);;All Files (*)"),
        )
        if not path_str:
            return
        path = Path(path_str)
        self.import_button.setEnabled(False)

        def work() -> KnownWordsImportResult | KnownWordsImportError:
            # Expected failures travel through on_done so the reason survives
            # (run_off_thread's on_error only receives a message string).
            try:
                return parse_known_words_file(path)
            except KnownWordsImportError as exc:
                return exc

        run_off_thread(self, work, self._on_import_parsed, self._on_import_failed)

    def _on_import_parsed(self, outcome: object) -> None:
        self.import_button.setEnabled(True)
        if isinstance(outcome, KnownWordsImportError):
            self._show_import_error(outcome)
            return
        if not isinstance(outcome, KnownWordsImportResult):  # pragma: no cover - defensive
            return
        if outcome.format_key == "generic":
            prompt = tr_format(
                self.tr(
                    "Detected: %1 — this file has no known/learning status; "
                    "all %2 entries will be imported.\n\nAdd %3 word(s) to your known list?"
                ),
                self._format_display_name(outcome.format_key),
                # A plain list has no known/unknown split, so its "entries" ARE the
                # imported words — report the deduplicated count (matching %3), not
                # the raw line count, which over-states on lists with duplicates.
                len(outcome.words),
                len(outcome.words),
            )
        else:
            prompt = tr_format(
                self.tr("Detected: %1 — %2 entries, %3 qualify as known.\n\nAdd %3 word(s) to your known list?"),
                self._format_display_name(outcome.format_key),
                outcome.total_entries,
                len(outcome.words),
            )
        reply = QMessageBox.question(
            self,
            self.tr("Import Known Words"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        added, already = self.apply_import(outcome)
        self._refresh()
        QMessageBox.information(
            self,
            self.tr("Import Complete"),
            tr_format(self.tr("Added %1 word(s) to your list. %2 were already in it."), added, already),
        )

    def _show_import_error(self, error: KnownWordsImportError) -> None:
        if error.reason == "no_known_words":
            message = tr_format(
                self.tr("Detected: %1 — but no entries in this file qualify as known."),
                self._format_display_name(error.format_key or "generic"),
            )
        elif error.reason == "unreadable":
            message = self.tr("The file could not be read.")
        else:
            message = self.tr(
                "File format not recognized. Supported: jpdb review export (JSON), "
                "Migaku word export (JSON/CSV), AnkiMorphs known morphs (CSV), "
                "plain word lists (one word per line)."
            )
        QMessageBox.warning(self, self.tr("Import Failed"), message)

    def _on_import_failed(self, message: str) -> None:
        self.import_button.setEnabled(True)
        QMessageBox.warning(
            self,
            self.tr("Import Failed"),
            tr_format(self.tr("Unexpected error while reading the file:\n%1"), message),
        )

    def export_to(self, path: Path) -> int:
        """Write the user words to ``path``, one per line (UTF-8). Returns the count."""
        words = sorted(self._db.get_words_by_source("user"))
        path.write_text("\n".join(words) + ("\n" if words else ""), encoding="utf-8")
        return len(words)

    def _on_export(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export Known Words"),
            str(Path(resolve_start_dir(None, file_mode=True)) / "known_words.txt"),
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return
        count = self.export_to(Path(path_str))
        QMessageBox.information(
            self,
            self.tr("Export Complete"),
            tr_format(self.tr("Exported %1 word(s) to:\n%2"), count, path_str),
        )

    def _on_reset(self) -> None:
        if self.word_list.count() == 0:
            return
        reply = QMessageBox.question(
            self,
            self.tr("Reset User List"),
            self.tr(
                "Remove ALL words you added to the local known words list? "
                "This cannot be undone. The Anki-synced cache is not affected."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.clear_user()
            self._refresh()

"""Dialog for managing the local user-curated known/ignore word list (Issue #42).

Shows the words the user added from the Word Curator (``source='user'``), lets
them remove entries, export the list to a plain-text file (one word per line, for
round-tripping back into jiten.moe), and reset it. The Anki-synced cache rows are
not editable here — only counted for context.
"""

from __future__ import annotations

from collections.abc import Callable
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
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.content_text import content_cell_font
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons
from anki_miner.gui.utils.qt_helpers import (
    add_min_max_buttons,
    configure_data_view,
    install_copy_rows,
)
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile
from anki_miner.services.known_word_db import KnownWordDB, normalize_lemma
from anki_miner.services.known_words_import import (
    KnownWordsImportError,
    KnownWordsImportResult,
    parse_known_words_file,
)
from anki_miner.utils.i18n import tr_format


class KnownWordsManagerDialog(ScreenIssueHost, QDialog):
    """View / remove / export / reset the user-curated known words list."""

    # Keyword-only additions accumulate here — do not drop existing keywords.
    def __init__(
        self,
        known_word_db: KnownWordDB,
        parent=None,
        *,
        language: str = "ja",
        content_style: ContentTextStyle | None = None,
    ):
        super().__init__(parent)
        self._db = known_word_db
        self._language = language
        # Every listed word is mined content: the face follows the mining
        # language. None keeps today's Japanese face for the ja default.
        self._content_style = content_style or get_profile(self._language).content_style
        self._dialog_generation = 0
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
        configure_data_view(self.word_list)
        install_copy_rows(self.word_list)
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
        self.reset_button = ModernButton(self.tr("Reset User List"), variant="critical")
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
        # The filter field holds Japanese, and Return is how an input method
        # commits a composition. With Close left as the default button, typing
        # kana into the filter closed the manager (D49). Esc still closes it.
        disown_default_buttons(self)
        self.install_issue_banner(layout)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Reload the user words from the DB and update the list + count label."""
        user_words = sorted(self._db.get_words_by_source("user"))
        self.word_list.clear()
        self.word_list.addItems(user_words)
        # Every entry is a mined word: the content face, and only the face —
        # a cell font carrying no size leaves the shared row height alone
        # (decision D45-B).
        cell_font = content_cell_font(self._content_style)
        for row in range(self.word_list.count()):
            item = self.word_list.item(row)
            if item is not None:
                item.setFont(cell_font)
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

    def done(self, result: int) -> None:
        """Close the dialog and invalidate unfinished async UI callbacks."""
        self._dialog_generation += 1
        super().done(result)

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

        The parsed words are folded to the same normal form the write uses:
        ``add_words`` normalizes internally, and the parser does not, so
        diffing raw against stored counted an NFD spelling of an already-known
        word as newly added and double-counted a file carrying both spellings.
        Display only — the rows written were always correct.
        """
        words = {normalize_lemma(word) for word in result.words}
        existing_user = self._db.get_words_by_source("user")
        new_to_list = words - existing_user
        self._db.add_words(words, source="user")
        return len(new_to_list), len(words) - len(new_to_list)

    def _on_import(self) -> None:

        def _on_picked(path_str: str) -> None:
            if not path_str:
                return
            path = Path(path_str)
            self.import_button.setEnabled(False)
            generation = self._dialog_generation

            def work() -> KnownWordsImportResult | KnownWordsImportError:
                # Expected failures travel through on_done so the reason survives
                # (run_off_thread's on_error only receives a message string).
                try:
                    from anki_miner.languages.registry import get_profile

                    return parse_known_words_file(path, encodings=get_profile(self._language).import_encodings)
                except KnownWordsImportError as exc:
                    return exc

            run_off_thread(
                self,
                work,
                lambda outcome: self._on_import_parsed(generation, outcome),
                lambda message: self._on_import_failed(generation, message),
            )

        file_dialogs.pick_open_file(
            self,
            self.tr("Import Known Words"),
            resolve_start_dir(None, file_mode=True),
            self.tr("Known word lists (*.csv *.txt *.json);;All Files (*)"),
            on_done=_on_picked,
        )

    def _on_import_parsed(self, generation: int, outcome: object) -> None:
        if generation != self._dialog_generation:
            return
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
        self.show_screen_issue(ScreenIssue(summary=message))

    def _on_import_failed(self, generation: int, message: str) -> None:
        if generation != self._dialog_generation:
            return
        self.import_button.setEnabled(True)
        self.show_screen_issue(ScreenIssue(summary=self.tr("That file could not be read."), details=message))

    def export_to(self, path: Path) -> int:
        """Write the user words to ``path``, one per line (UTF-8). Returns the count."""
        words = sorted(self._db.get_words_by_source("user"))
        path.write_text("\n".join(words) + ("\n" if words else ""), encoding="utf-8")
        return len(words)

    def _on_export(self) -> None:

        def _on_picked(path_str: str) -> None:
            if not path_str:
                return
            self.export_button.setEnabled(False)
            generation = self._dialog_generation
            run_off_thread(
                self,
                lambda: self.export_to(Path(path_str)),
                lambda count: self._on_export_succeeded(
                    generation,
                    lambda: QMessageBox.information(
                        self,
                        self.tr("Export Complete"),
                        tr_format(self.tr("Exported %1 word(s) to:\n%2"), count, path_str),
                    ),
                ),
                lambda message: self._on_export_failed(generation, path_str, message),
                on_finished=lambda: self._on_export_finished(generation),
            )

        file_dialogs.pick_save_file(
            self,
            self.tr("Export Known Words"),
            str(Path(resolve_start_dir(None, file_mode=True)) / "known_words.txt"),
            "Text Files (*.txt);;All Files (*)",
            on_done=_on_picked,
        )

    def _on_export_succeeded(self, generation: int, notify: Callable[[], object]) -> None:
        if generation != self._dialog_generation:
            return
        self.clear_screen_issue()
        notify()

    def _on_export_failed(self, generation: int, path_str: str, message: str) -> None:
        if generation != self._dialog_generation:
            return
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr("The known words list could not be exported."),
                details=f"{path_str}: {message}",
                action_id="known-words.export-retry",
                action_text=self.tr("Retry"),
            ),
            action=self._on_export,
        )

    def _on_export_finished(self, generation: int) -> None:
        if generation != self._dialog_generation:
            return
        self.export_button.setEnabled(True)

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

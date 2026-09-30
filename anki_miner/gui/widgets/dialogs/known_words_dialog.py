"""Dialog for managing the local user-curated known/ignore word list (Issue #42).

Shows the words the user added from the Word Curator (``source='user'``), lets
them remove entries, export the list to a plain-text file (one word per line, for
round-tripping back into jiten.moe), and reset it. The Anki-synced cache rows are
not editable here — only counted for context.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.content_text import content_cell_font
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons
from anki_miner.gui.utils.qt_helpers import (
    configure_data_view,
    install_copy_rows,
)
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.known_words_import import (
    KnownWordsImportError,
    KnownWordsImportResult,
    parse_known_words_file,
)
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.subtitle_encoding import script_check_kwarg

logger = logging.getLogger(__name__)


class KnownWordsManagerDialog(ScreenIssueHost, EnhancedDialog):
    """View / remove / export / reset the user-curated known words list."""

    # Keyword-only additions accumulate here — do not drop existing keywords.
    def __init__(
        self,
        known_word_db: KnownWordDB,
        parent=None,
        *,
        language: str = "ja",
        content_style: ContentTextStyle | None = None,
        excluded_decks: tuple[str, ...] = (),
        on_rebuild: Callable[[Callable[[], None]], None] | None = None,
        rebuild_enabled: bool = False,
    ):
        super().__init__(parent, title=self.tr("Manage Known Words"))
        self._db = known_word_db
        self._language = language
        self._excluded_decks = excluded_decks
        # Every listed word is mined content: the face follows the mining
        # language. None keeps today's Japanese face for the ja default.
        self._content_style = content_style or get_profile(self._language).content_style
        self._dialog_generation = 0
        # Rebuild runs in SettingsTab (confirm, per-language path, off-thread
        # clear); this dialog only offers the button and hears when it ends.
        self._on_rebuild = on_rebuild
        self._rebuild_enabled = rebuild_enabled
        self._rebuild_in_flight = False
        # The list may never have been written if the user only just enabled the
        # feature — initialize so reads/writes don't hit a missing file.
        self._db.initialize()
        # Not _setup_ui: EnhancedDialog.__init__ already ran its own frame builder.
        self._build_content()
        self._refresh()

    def _build_content(self) -> None:
        # No explicit minimum width: the action row sets it (Z.5).
        self.setMinimumHeight(520)
        self.set_header(
            "",
            self.tr("Local Known Words"),
            self.tr("Words you added from the Word Curator. Ignored on every run and kept across cache rebuilds."),
        )
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.sm)
        # S15: the scan's exclusions are this language's own, and they are the
        # only thing that keeps another language's deck out of its known words.
        if self._excluded_decks:
            exclusions_text = tr_format(
                self.tr("Decks this language's known-words scan skips: %1. Change them in Settings → Word Filters."),
                ", ".join(self._excluded_decks),
            )
        else:
            exclusions_text = self.tr(
                "Every deck is scanned for this language, including decks in another language written in the same "
                "script. Exclude them in Settings → Word Filters."
            )
        self.exclusions_label = QLabel(exclusions_text)
        self.exclusions_label.setObjectName("helper-text")
        self.exclusions_label.setWordWrap(True)
        layout.addWidget(self.exclusions_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Filter…"))
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        self.word_list = QListWidget()
        self.word_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        configure_data_view(self.word_list)
        install_copy_rows(self.word_list)
        layout.addWidget(self.word_list)

        # The count, and Rebuild beside the "cached from Anki" number it clears
        # (C13; moved here from Word Filters). The label stays: D20 item 3 kept it.
        count_row = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setObjectName("helper-text")
        count_row.addWidget(self.count_label, 1)
        self.rebuild_button = ModernButton(self.tr("Rebuild Known Words DB"), variant="secondary")
        self.rebuild_button.setToolTip(
            self.tr(
                "Clear the local known-words cache so it re-syncs from Anki on the "
                "next run. Needed for deck exclusions to take effect when the "
                "local cache is enabled."
            )
        )
        self.rebuild_button.clicked.connect(self._on_rebuild_clicked)
        self.rebuild_button.setVisible(self._on_rebuild is not None)
        count_row.addWidget(self.rebuild_button)
        layout.addLayout(count_row)
        self._sync_rebuild_button()

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
        layout.addLayout(buttons)
        self.add_content(content, 1)
        # C19: Close is the only way out, so it is the footer's one primary.
        self.add_close_button()
        # The filter field holds Japanese, and Return is how an input method
        # commits a composition. With Close left as the default button, typing
        # kana into the filter closed the manager (D49). Esc still closes it.
        disown_default_buttons(self)
        self.install_issue_banner(self._main_layout, 1)

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
        self.count_label.setText(tr_format(self.tr("User words: %1 · cached from Anki: %2"), len(user_words), cached))

    def _sync_rebuild_button(self) -> None:
        """Enabled only while the cache is on and no rebuild runs (the old guard, C13)."""
        self.rebuild_button.setEnabled(self._rebuild_enabled and not self._rebuild_in_flight)

    def _on_rebuild_clicked(self) -> None:
        if self._on_rebuild is None or self._rebuild_in_flight:
            return
        self._rebuild_in_flight = True
        self._sync_rebuild_button()
        generation = self._dialog_generation

        def done() -> None:
            # A rebuild can outlive the dialog; a closed dialog ignores it.
            if generation != self._dialog_generation:
                return
            self._rebuild_in_flight = False
            self._sync_rebuild_button()
            self._refresh()

        self._on_rebuild(done)

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

        The parsed words are folded to the key the write uses
        (:meth:`KnownWordDB.normalize_key`): ``add_words`` normalizes
        internally, and the parser does not, so
        diffing raw against stored counted an NFD spelling of an already-known
        word as newly added and double-counted a file carrying both spellings.
        Display only — the rows written were always correct.
        """
        words = {self._db.normalize_key(word) for word in result.words}
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

                    profile = get_profile(self._language)
                    return parse_known_words_file(
                        path,
                        encodings=profile.import_encodings,
                        **script_check_kwarg(profile.import_encodings, profile.script),
                    )
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
                self.tr("Detected: %1 — every entry is imported.\n\nWords to add: %2. Continue?"),
                self._format_display_name(outcome.format_key),
                # A plain list has no known/unknown split, so its "entries" ARE the
                # imported words — report the deduplicated count, not the raw line
                # count, which over-states on lists with duplicates.
                len(outcome.words),
            )
        else:
            prompt = tr_format(
                self.tr("Detected: %1 — %2 entries, %3 qualify as known.\n\nWords to add: %3. Continue?"),
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
        log_summary(
            logger,
            "Confirm",
            action="import_known_words",
            answer="yes" if reply == QMessageBox.StandardButton.Yes else "no",
            scope=f"{outcome.format_key} {len(outcome.words)} words",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        added, already = self.apply_import(outcome)
        self._refresh()
        QMessageBox.information(
            self,
            self.tr("Import Complete"),
            tr_format(self.tr("Added to your list: %1. Already in it: %2."), added, already),
        )

    def _show_import_error(self, error: KnownWordsImportError) -> None:
        if error.reason == "no_known_words":
            message = tr_format(
                self.tr("Detected: %1 — but no entries in this file qualify as known."),
                self._format_display_name(error.format_key or "generic"),
            )
        elif error.reason == "too_large":
            message = self.tr("That file is too large to import.")
        elif error.reason == "unreadable":
            message = self.tr("The file could not be read.")
        elif error.reason == "undecodable":
            message = self.tr("That file's text encoding could not be read.")
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
        # Every expected failure is returned through on_done as a
        # KnownWordsImportError, so anything landing here is an unexpected
        # exception with no established cause (A8-35) — name the outcome, not
        # a step the code never reached.
        self.show_screen_issue(ScreenIssue(summary=self.tr("That file could not be imported."), details=message))

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
                        tr_format(self.tr("Exported %1 words to:\n%2"), count, path_str),
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
        log_summary(
            logger,
            "Confirm",
            action="reset_user_known_words",
            answer="yes" if reply == QMessageBox.StandardButton.Yes else "no",
            scope=f"{self.word_list.count()} words",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.clear_user()
            self._refresh()

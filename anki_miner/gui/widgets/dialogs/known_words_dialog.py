"""Manage Known Words: browse, search, import, export and edit one language's known words.

Lists every row of the active language's known-words DB — words you added
(``source='user'``), words Anki Miner mined, and words cached from Anki — with
their source and date added (WP-06). Only your own words can be removed here:
Anki rows come back on the next sync, and mined rows leave with Undo. Import
reads other tools' exports; Export writes the rows on screen.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QCoreApplication, QModelIndex, QObject, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.content_text import ContentCellDelegate, content_cell_font
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons
from anki_miner.gui.utils.qt_helpers import (
    COPY_ROLE,
    configure_data_view,
    install_copy_rows,
)
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile, language_display_name
from anki_miner.services.known_word_db import KnownWordDB, KnownWordRow
from anki_miner.services.known_words_import import (
    KnownWordsImportError,
    KnownWordsImportResult,
    parse_known_words_file,
)
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.subtitle_encoding import script_check_kwarg

logger = logging.getLogger(__name__)


#: An invalid index, the "no parent" of a flat table (a call in a default
#: argument would be evaluated once at import anyway; named for B008).
_NO_PARENT = QModelIndex()


class KnownWordsTableModel(QAbstractTableModel):
    """One language's known-words rows: word, source, date added (WP-06).

    A model over a plain list, not one QTableWidgetItem per cell: a large
    collection caches about 100k words, and three items each would make the
    dialog take seconds to open.
    """

    WORD, SOURCE, ADDED = range(3)

    def __init__(
        self,
        parent: QObject,
        *,
        style: Callable[[], ContentTextStyle],
        source_label: Callable[[str], str],
    ) -> None:
        super().__init__(parent)
        self._rows: list[KnownWordRow] = []
        self._style = style
        self._source_label = source_label
        self._word_font = content_cell_font(style())

    def set_rows(self, rows: list[KnownWordRow]) -> None:
        """Replace every row (the dialog's refresh)."""
        self.beginResetModel()
        self._rows = list(rows)
        self._word_font = content_cell_font(self._style())
        self.endResetModel()

    def row_at(self, row: int) -> KnownWordRow:
        """The source row behind a model row number."""
        return self._rows[row]

    def rowCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else 3

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        entry = self._rows[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, COPY_ROLE):
            if column == self.WORD:
                return entry.lemma
            if column == self.SOURCE:
                return self._source_label(entry.source)
            # The date reads as a date; hover and copy carry the stored UTC time.
            return entry.added_at[:10] if role == Qt.ItemDataRole.DisplayRole else entry.added_at
        if role == Qt.ItemDataRole.FontRole and column == self.WORD:
            return self._word_font
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        # The model is not a class the catalog knows, so the headers name the
        # dialog's context, as a literal: pylupdate6 drops a context passed by name.
        if section == self.WORD:
            return QCoreApplication.translate("KnownWordsManagerDialog", "Word")
        if section == self.SOURCE:
            return QCoreApplication.translate("KnownWordsManagerDialog", "Source")
        if section == self.ADDED:
            return QCoreApplication.translate("KnownWordsManagerDialog", "Added")
        return None


class KnownWordsFilter(QSortFilterProxyModel):
    """The browser's search text and source filter over :class:`KnownWordsTableModel`."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._needle = ""
        self._source = ""

    def set_needle(self, text: str) -> None:
        """Show only words containing ``text`` (case-insensitive); "" shows all."""
        self._needle = text.casefold()
        self.invalidateFilter()

    def set_source(self, source: str) -> None:
        """Show only rows from ``source`` ("user", "mined", "anki"); "" shows every source."""
        self._source = source
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, KnownWordsTableModel):
            return True
        entry = model.row_at(source_row)
        if self._source and entry.source != self._source:
            return False
        return not self._needle or self._needle in entry.lemma.casefold()


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
            self.tr(
                "Every word this language counts as known, and where it came from. Words you added from "
                "the Word Curator stay known on every run and across cache rebuilds."
            ),
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

        # Built once: the model asks for a source name on every paint and sort.
        self._source_labels = {
            "user": self.tr("Added by you"),
            "mined": self.tr("Mined by Anki Miner"),
            "anki": self.tr("From Anki"),
        }
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Filter…"))
        self.search_input.textChanged.connect(self._on_search_changed)
        # WP-06: which source to show. It opens on the words you added, the list
        # this dialog always showed, so the plain-list export round trip holds.
        self.source_filter = QComboBox()
        self.source_filter.addItem(self.tr("All sources"), "")
        self.source_filter.addItem(self._source_labels["user"], "user")
        self.source_filter.addItem(self._source_labels["mined"], "mined")
        self.source_filter.addItem(self._source_labels["anki"], "anki")
        self.source_filter.setCurrentIndex(1)
        self.source_filter.currentIndexChanged.connect(self._on_source_changed)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(SPACING.sm)
        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.source_filter)
        layout.addLayout(filter_row)

        self._model = KnownWordsTableModel(self, style=lambda: self._content_style, source_label=self._source_label)
        self._proxy = KnownWordsFilter(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.set_source("user")
        self.word_table = QTableView()
        self.word_table.setModel(self._proxy)
        self.word_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.word_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.word_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        configure_data_view(self.word_table)
        install_copy_rows(self.word_table)
        # Every word is mined content: its cells lay out in the mining
        # language's direction (S21), the chrome columns stay as they are.
        self.word_table.setItemDelegateForColumn(
            KnownWordsTableModel.WORD, ContentCellDelegate(self.word_table, lambda: self._content_style)
        )
        header = self.word_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(KnownWordsTableModel.WORD, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(KnownWordsTableModel.SOURCE, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(KnownWordsTableModel.ADDED, QHeaderView.ResizeMode.ResizeToContents)
        self.word_table.setSortingEnabled(True)
        self.word_table.sortByColumn(KnownWordsTableModel.WORD, Qt.SortOrder.AscendingOrder)
        selection = self.word_table.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self._sync_remove_button)
        layout.addWidget(self.word_table)

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
        self.remove_button.setToolTip(
            self.tr(
                "Removes words you added. Words from Anki come back on the next run, and mined words leave with Undo."
            )
        )
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
        """Reload every row from the DB and update the table and the count label."""
        rows = self._db.list_rows()
        self._model.set_rows(rows)
        user_count = sum(1 for row in rows if row.source == "user")
        self.count_label.setText(
            tr_format(self.tr("User words: %1 · cached from Anki: %2"), user_count, len(rows) - user_count)
        )
        self._sync_remove_button()

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
        self._proxy.set_needle(text)
        self._sync_remove_button()

    def _source_label(self, source: str) -> str:
        """The translated name of a row's source; an unknown source shows as stored."""
        return self._source_labels.get(source, source)

    def _on_source_changed(self, _index: int) -> None:
        self._proxy.set_source(str(self.source_filter.currentData() or ""))
        self._sync_remove_button()

    def visible_rows(self) -> list[KnownWordRow]:
        """The rows on screen now (search and source filter applied), top to bottom."""
        return [
            self._model.row_at(self._proxy.mapToSource(self._proxy.index(row, 0)).row())
            for row in range(self._proxy.rowCount())
        ]

    def _selected_rows(self) -> list[KnownWordRow]:
        selection = self.word_table.selectionModel()
        if selection is None:
            return []
        return [
            self._model.row_at(self._proxy.mapToSource(index).row())
            for index in selection.selectedRows(KnownWordsTableModel.WORD)
        ]

    def _selected_user_words(self) -> set[str]:
        """Selected words you added: the only rows Remove may touch (R10)."""
        return {row.lemma for row in self._selected_rows() if row.source == "user"}

    def _sync_remove_button(self, *_args: object) -> None:
        self.remove_button.setEnabled(bool(self._selected_user_words()))

    def done(self, result: int) -> None:
        """Close the dialog and invalidate unfinished async UI callbacks."""
        self._dialog_generation += 1
        super().done(result)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_remove(self) -> None:
        words = self._selected_user_words()
        if not words:
            return
        # source="user": an Anki row would come back on the next sync, and a
        # mined row belongs to its run's Undo.
        self._db.remove_words(words, source="user")
        self._refresh()

    def _format_display_name(self, format_key: str) -> str:
        """Translated label for a parser format key (keep in lockstep with FORMAT_KEYS)."""
        labels = {
            "jpdb": self.tr("jpdb review export"),
            "migaku_json": self.tr("Migaku word export"),
            "migaku_legacy": self.tr("Migaku legacy add-on backup"),
            "ankimorphs": self.tr("AnkiMorphs known morphs"),
            "migaku_csv": self.tr("Migaku word export (CSV)"),
            "kindle_vocab": self.tr("Kindle Vocabulary Builder"),
            "lute_csv": self.tr("Lute or LWT term export"),
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
                        language=self._language,
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
            self.tr("Known word lists (*.csv *.tsv *.txt *.json *.db);;All Files (*)"),
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
        elif error.reason == "other_language":
            # Ruling C3: a Lute/LWT file that holds only other languages says
            # which, so a Spanish learner with a Japanese export knows why.
            message = tr_format(
                self.tr("This file has no words for %1. It contains: %2."),
                language_display_name(self._language),
                ", ".join(error.languages),
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
                "Kindle Vocabulary Builder (vocab.db), Lute or LWT term export (CSV/TSV), "
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
        user_count = len(self._db.get_words_by_source("user"))
        if user_count == 0:
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
            scope=f"{user_count} words",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.clear_user()
            self._refresh()

"""Card Backfill tool tab (Tools → Card Backfill).

Bulk-fills pitch/frequency/definition/glossary/reading fields on EXISTING
miner cards after the user installs new resources. Two-step flow: Scan
(read-only, off-thread) builds a :class:`BackfillPlan` shown in a preview
table; Apply writes exactly the previewed values and tags touched notes
``anki-miner::backfill``. Fill-only-empty by default; overwrite is an explicit
checkbox.

Plain ``QWidget`` (not ``_ToolTabBase`` — that base is file-processing
chrome). Follows the condense-tab worker conventions: the active worker lives
on ``self.worker_thread``, ``iter_close_workers()`` yields it for the
app-close join, and ``update_config`` re-gates the field checkboxes AND drops
any held plan (its computed values are config-stale).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.workers.backfill_worker import BackfillApplyWorker, BackfillScanWorker
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import FetchDecksWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.card_backfiller import (
    BACKFILL_TAG,
    FIELD_GROUPS,
    BackfillOptions,
    BackfillPlan,
    BackfillResult,
)

logger = logging.getLogger(__name__)

_PREVIEW_ROW_CAP = 500
_CELL_ELIDE = 120


class CardBackfillTab(QWidget):
    """Scan → preview table → Apply, over the configured note type."""

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.worker_thread: BackfillScanWorker | BackfillApplyWorker | None = None
        self._plan: BackfillPlan | None = None
        self._decks_requested = False
        self._deck_worker: SingleCallWorker | None = None
        self._build_ui()
        self._refresh_checkbox_gates()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(SectionHeader(self.tr("Card Backfill")))
        hint = QLabel(
            self.tr(
                "Fill missing fields on cards you mined earlier, using the currently "
                "installed dictionaries, frequency sources and pitch data. "
                "For very large collections, run per-deck. "
                "Overwrite mode may need a follow-up Restyle to refresh card styling."
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        deck_row = QHBoxLayout()
        deck_row.addWidget(QLabel(self.tr("Deck:")))
        self.deck_combo = QComboBox()
        self.deck_combo.addItem(self.tr("All decks"))
        deck_row.addWidget(self.deck_combo, stretch=1)
        layout.addLayout(deck_row)

        layout.addWidget(SectionHeader(self.tr("Fields to fill")))
        self.field_checkboxes: dict[str, QCheckBox] = {}
        labels = {
            "pitch": self.tr("Pitch accent (graph + text)"),
            "frequency": self.tr("Frequency (display + sort)"),
            "definition": self.tr("Definitions"),
            "glossary": self.tr("Glossary"),
            "reading": self.tr("Reading + furigana"),
        }
        for group in FIELD_GROUPS:
            checkbox = QCheckBox(labels[group])
            if group == "reading":
                checkbox.setToolTip(
                    self.tr(
                        "Fills furigana from an existing reading and vice versa; " "does not generate new readings."
                    )
                )
            self.field_checkboxes[group] = checkbox
            layout.addWidget(checkbox)

        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing values"))
        layout.addWidget(self.overwrite_checkbox)

        buttons = QHBoxLayout()
        self.scan_button = ModernButton(self.tr("Scan"), variant="primary")
        self.scan_button.clicked.connect(self._start_scan)
        buttons.addWidget(self.scan_button)
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setHorizontalHeaderLabels(
            [self.tr("Expression"), self.tr("Field"), self.tr("Current"), self.tr("New")]
        )
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.preview_table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        layout.addWidget(self.preview_table, stretch=1)

        bottom = QHBoxLayout()
        self.apply_button = ModernButton(self.tr("Apply"), variant="primary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._start_apply)
        bottom.addWidget(self.apply_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # Gating / config
    # ------------------------------------------------------------------

    def _refresh_checkbox_gates(self) -> None:
        """Enable each group per its anki_fields mapping.

        Non-reading groups enable when AT LEAST ONE of their keys is mapped
        (per-field compute skips unmapped keys). The reading group is pure
        cross-fill, so it needs BOTH fields mapped to ever do anything —
        an enabled-but-inert checkbox would be dishonest UI.
        """
        for group, keys in FIELD_GROUPS.items():
            mapped = [bool(self.config.anki_fields.get(key)) for key in keys]
            enabled = all(mapped) if group == "reading" else any(mapped)
            checkbox = self.field_checkboxes[group]
            checkbox.setEnabled(enabled)
            if not enabled:
                checkbox.setChecked(False)
                checkbox.setToolTip(self.tr("Map this field in Settings → Anki"))

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new config: re-gate checkboxes and drop any held plan.

        The plan's computed values (field names, style CSS, lookups) are all
        config-derived, so a config change makes them stale — never apply them.
        """
        self.config = config
        self._plan = None
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        self._refresh_checkbox_gates()

    def iter_close_workers(self) -> Iterator[BackfillScanWorker | BackfillApplyWorker | SingleCallWorker]:
        if self.worker_thread is not None and self.worker_thread.isRunning():
            yield self.worker_thread
        # The lazy deck-fetch QThread runs a blocking get_deck_names (timeout 15s);
        # abandoning it to Qt teardown aborts with "QThread: Destroyed while
        # thread is still running", so surface it for the close-join policy too.
        if self._deck_worker is not None and self._deck_worker.isRunning():
            yield self._deck_worker

    # ------------------------------------------------------------------
    # Deck dropdown (lazy fetch on first show)
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._decks_requested:
            self._decks_requested = True
            self._load_decks()

    def _load_decks(self) -> None:
        try:
            service = AnkiService(self.config)
        except ValueError:
            return  # mapping incomplete; deck filter stays "All decks"
        worker = FetchDecksWorker(service, parent=self)
        worker.result_ready.connect(self._on_decks_fetched)
        worker.error.connect(lambda _msg: self._on_decks_fetched([]))
        self._deck_worker = worker
        worker.start()

    def _on_decks_fetched(self, decks: list) -> None:
        if decks:
            self.deck_combo.addItems([str(d) for d in decks])
        else:
            self.status_label.setText(self.tr("Couldn't fetch deck names from Anki — scanning all decks."))

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _selected_field_keys(self) -> frozenset[str]:
        keys: set[str] = set()
        for group, checkbox in self.field_checkboxes.items():
            if not checkbox.isChecked():
                continue
            keys.update(key for key in FIELD_GROUPS[group] if self.config.anki_fields.get(key))
        return frozenset(keys)

    def _start_scan(self) -> None:
        field_keys = self._selected_field_keys()
        if not field_keys:
            self.status_label.setText(self.tr("Select at least one field group to fill."))
            return
        deck = self.deck_combo.currentText() if self.deck_combo.currentIndex() > 0 else None
        options = BackfillOptions(
            field_keys=field_keys,
            deck=deck,
            overwrite=self.overwrite_checkbox.isChecked(),
        )
        worker = BackfillScanWorker(self.config, options, parent=self)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_scan_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        self._set_running(True)
        self.status_label.setText(self.tr("Scanning…"))
        worker.start()

    def _on_scan_finished(self, plan: BackfillPlan) -> None:
        self._plan = plan if plan.notes else None
        self._populate_preview(plan)
        self.apply_button.setEnabled(bool(plan.notes))
        self.status_label.setText("")

    def _populate_preview(self, plan: BackfillPlan) -> None:
        rows = [(note.expression, change) for note in plan.notes for change in note.changes][:_PREVIEW_ROW_CAP]
        self.preview_table.setRowCount(len(rows))
        for row, (expression, change) in enumerate(rows):
            # Only new_value is raw field markup (HTML/SVG) — strip it for the
            # cell and show a marker when it has no text nodes (a pitch-accent
            # SVG). The other three columns are already display-safe: expression
            # and field_name are plain text, and old_display was _display()-
            # stripped when the plan was built, so re-stripping it here would
            # double-truncate.
            new_display = self._strip_cell(change.new_value)
            if not new_display and change.new_value:
                new_display = self.tr("(formatted content)")
            for col, text in enumerate((expression, change.field_name, change.old_display, new_display)):
                item = QTableWidgetItem(text[:_CELL_ELIDE] + "…" if len(text) > _CELL_ELIDE else text)
                item.setToolTip(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.preview_table.setItem(row, col, item)
        self.summary_label.setText(self._summary_text(plan, len(rows)))

    @staticmethod
    def _strip_cell(text: str) -> str:
        from anki_miner.services.card_backfiller import _display

        return _display(text)

    def _summary_text(self, plan: BackfillPlan, shown_rows: int) -> str:
        parts: list[str] = []
        if plan.notes:
            parts.append(
                self.tr("{fields} field(s) across {notes} note(s) will be filled.").format(
                    fields=plan.total_field_changes, notes=len(plan.notes)
                )
            )
            if plan.total_field_changes > shown_rows:
                parts.append(self.tr("Showing first {rows} rows.").format(rows=shown_rows))
        else:
            parts.append(self.tr("Nothing to fill — all selected fields already have values."))
        if plan.sentinel_only_sorts:
            parts.append(
                self.tr("{count} sort value(s) are the 9999999 no-frequency-found placeholder.").format(
                    count=plan.sentinel_only_sorts
                )
            )
        if plan.unavailable_fields:
            parts.append(
                self.tr("Skipped (resource not loaded): {fields}.").format(fields=", ".join(plan.unavailable_fields))
            )
        if plan.skipped_no_identity:
            parts.append(
                self.tr("{count} note(s) skipped — empty Expression field.").format(count=plan.skipped_no_identity)
            )
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _start_apply(self) -> None:
        plan = self._plan
        if plan is None:
            return
        if plan.config_version != self.config.config_version:
            self._plan = None
            self.preview_table.setRowCount(0)
            self.apply_button.setEnabled(False)
            self.summary_label.setText("")
            self.status_label.setText(self.tr("Settings changed since this scan; re-scan before applying."))
            return
        answer = QMessageBox.question(
            self,
            self.tr("Apply backfill?"),
            self.tr(
                "Close Anki's card browser and note editors first.\n\n"
                "This will modify {notes} note(s) ({fields} field(s)) and tag them "
                "{tag}. Continue?"
            ).format(notes=len(plan.notes), fields=plan.total_field_changes, tag=BACKFILL_TAG),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = BackfillApplyWorker(self.config, plan, parent=self)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_apply_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        self._set_running(True)
        self.status_label.setText(self.tr("Applying…"))
        worker.start()

    def _on_apply_finished(self, result: BackfillResult) -> None:
        self._plan = None
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        parts = [
            self.tr("Filled {fields} field(s) on {notes} note(s). Tagged {tag}.").format(
                fields=result.fields_filled, notes=result.notes_updated, tag=BACKFILL_TAG
            )
        ]
        if result.skipped_stale:
            parts.append(
                self.tr("{count} skipped — changed or deleted since the scan.").format(count=result.skipped_stale)
            )
        if result.tagged < result.notes_updated:
            parts.append(self.tr("Tagging failed for some notes (see log)."))
        self.status_label.setText(" ".join(parts))

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.apply_button.setEnabled(not running and self._plan is not None)
        self.cancel_button.setEnabled(running)
        for checkbox in self.field_checkboxes.values():
            checkbox.setEnabled(not running)
        self.overwrite_checkbox.setEnabled(not running)
        self.deck_combo.setEnabled(not running)
        self.progress_bar.setVisible(running)
        if running:
            self.progress_bar.setRange(0, 0)
        if not running:
            self._refresh_checkbox_gates()

    def _cancel(self) -> None:
        if self.worker_thread is not None and self.worker_thread.isRunning():
            self.worker_thread.cancel()
            self.status_label.setText(self.tr("Cancelling…"))

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    def _on_worker_error(self, message: str) -> None:
        self._set_running(False)
        self.status_label.setText(message)

    def _on_worker_finished(self) -> None:
        self._set_running(False)
        self.worker_thread = None

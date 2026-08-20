"""Card Backfill tool tab (Utilities → Card Backfill).

Bulk-fills pitch/frequency/definition/glossary/reading fields on EXISTING
miner notes after the user installs new resources. Two-step flow: Scan
(read-only, off-thread) builds a :class:`BackfillPlan` shown in a preview
table; the update writes exactly the previewed values and tags touched notes
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
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.fonts import japanese_cell_font
from anki_miner.gui.utils.keyboard_shortcuts import primary_action_shortcut
from anki_miner.gui.utils.qt_helpers import (
    CellRole,
    configure_data_view,
    data_row_height,
    install_copy_rows,
    install_no_scroll_on_inputs,
    make_table_item,
    urls_from_event,
)
from anki_miner.gui.widgets.base import (
    PageWidth,
    TaskPublisherMixin,
    capped_page_column,
    install_workflow_shell,
)
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.enhanced.modern_button import ButtonVariant
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
#: How much of the plan the preview must always show. Counted in rows rather
#: than pixels: a flat pixel floor holds fewer and fewer rows as the text scale
#: grows, which is Issue #102's class of bug rather than its fix.
PREVIEW_MIN_VISIBLE_ROWS = 8


def _set_variant(button: ModernButton, variant: ButtonVariant) -> None:
    """Re-role a button in place, using only the variants D41 already defines.

    ``ModernButton`` carries its role in its object name, and Qt caches the
    resolved stylesheet per widget, so the name change has to be followed by an
    unpolish/polish or the button keeps painting its old role.
    """
    if button.objectName() == variant:
        return
    button.setObjectName(variant)
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)


class CardBackfillTab(TaskPublisherMixin, QWidget):
    """Scan → preview table → Apply, over the configured note type."""

    #: Tables and queue rows genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the pinned
    #: bar gets progress and a clock (D17, D22). Scan and Apply are two runs of
    #: the same screen, so they share one id and supersede each other.
    TASK_ID = "tools.backfill"
    TASK_OWNER = CapabilityTarget("subtitles", "backfill")

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.worker_thread: BackfillScanWorker | BackfillApplyWorker | None = None
        self._plan: BackfillPlan | None = None
        self._scan_warnings: tuple[str, ...] = ()
        self._decks_requested = False
        self._deck_worker: SingleCallWorker | None = None
        # Set by the error slot, read when the thread ends: an error arrives
        # before ``finished``, which is where the run is closed out.
        self._run_failed = False
        self._build_ui()
        self._refresh_checkbox_gates()
        # Drops are answered, not swallowed (D50): this screen accepts the drag
        # only so it can say what it actually works on.
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Issue #102: the fixed chrome above the table (headers, hint, checkboxes,
        # buttons) can consume the whole logical height on scaled displays. The
        # table keeps a hard floor and the tab scrolls instead of crushing it.
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(SectionHeader(self.tr("Card Backfill")))
        hint = QLabel(
            self.tr(
                "Fill missing fields on notes you mined earlier, using the currently "
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
                    self.tr("Fills furigana from an existing reading and vice versa; does not generate new readings.")
                )
            self.field_checkboxes[group] = checkbox
            layout.addWidget(checkbox)

        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing values"))
        layout.addWidget(self.overwrite_checkbox)

        # Scan, Apply and Cancel all live in the pinned bar (D6). Scan is the
        # primary until a preview exists; after that Apply takes over and Scan
        # stays reachable as the quiet way to rescan.
        self.scan_button = ModernButton(self.tr("Scan Anki (read-only)"), variant="primary")
        self.scan_button.clicked.connect(self._start_scan)
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setHorizontalHeaderLabels(
            [self.tr("Expression"), self.tr("Field"), self.tr("Current"), self.tr("New")]
        )
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Every column is prose, so ordering is alphabetical either way -- but
        # a 500-row preview is unreadable without being able to gather one
        # field's changes together.
        self.preview_table.setSortingEnabled(True)
        configure_data_view(self.preview_table)
        install_copy_rows(self.preview_table)
        header = self.preview_table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
            # Sorting is available, but the preview opens in plan order -- the
            # order the writes will happen in. An indicator-less header sorts
            # nothing until the user asks; without this, merely enabling sorting
            # reorders the plan behind their back.
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._apply_preview_height_floor()
        layout.addWidget(self.preview_table, stretch=1)

        self.apply_button = ModernButton(self.tr("Update Notes in Anki"), variant="secondary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._start_apply)

        install_no_scroll_on_inputs(container)

        scroll_area = QScrollArea()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # Backfill has no activity log, so Activity is hidden rather than opened
        # onto an empty panel.
        self.action_bar = install_workflow_shell(outer, scroll_area, container, self.PAGE_WIDTH, log=None)
        # The run's own status line and bar go beside the buttons that produce
        # them, on the same capped column, instead of scrolling away under a
        # 240px preview table.
        outer.insertWidget(outer.count() - 1, capped_page_column(self._create_run_status(), self.PAGE_WIDTH))
        self._sync_action_prominence()
        # Ctrl+Enter runs whichever verb the stage is showing (D48-B): Scan
        # before a plan exists, Apply after. `_sync_action_prominence` is what
        # moves the primary, and the bar is read at press time, so the shortcut
        # follows without knowing the stage itself.
        primary_action_shortcut(self, self.action_bar.trigger_primary)

    def _apply_preview_height_floor(self) -> None:
        """Floor the preview at whole rows of the font it is actually rendering.

        Issue #102 gave the table a flat 240px floor so the chrome above could
        not crush it. That floor holds eight rows at the default text size and
        four at 150%, so the same crushing returns the moment the user scales
        text. Measuring the floor in rows keeps the guarantee the issue asked
        for at every scale.
        """
        header = self.preview_table.horizontalHeader()
        header_h = header.sizeHint().height() if header is not None else 0
        frame = 2 * self.preview_table.frameWidth()
        self.preview_table.setMinimumHeight(
            header_h + frame + PREVIEW_MIN_VISIBLE_ROWS * data_row_height(self.preview_table)
        )

    def changeEvent(self, a0) -> None:  # noqa: N802 - Qt override
        """Re-derive the preview's row metrics when the UI text size changes.

        Text size applies live, so a row height and a floor computed once at
        construction are stale from the next Settings save onward.
        """
        from PyQt6.QtCore import QEvent

        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.FontChange and hasattr(self, "preview_table"):
            configure_data_view(self.preview_table)
            self._apply_preview_height_floor()

    def _create_run_status(self) -> QWidget:
        """The one-line status and thin bar that sit directly above the actions."""
        strip = QWidget()
        strip_layout = QVBoxLayout(strip)
        strip_layout.setContentsMargins(SPACING.sm, 0, SPACING.sm, 0)
        strip_layout.setSpacing(SPACING.xxs)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        strip_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        strip_layout.addWidget(self.status_label)

        return strip

    def _sync_action_prominence(self) -> None:
        """Put the action the user can actually take next on the right.

        Before a valid preview there is only one honest move — Scan — and Apply
        has nothing to apply. Once a preview exists Apply becomes the point of
        the screen, and Scan stays visible so a rescan never needs the page
        rebuilt.
        """
        has_plan = self._plan is not None
        primary = self.apply_button if has_plan else self.scan_button
        quiet = self.scan_button if has_plan else self.apply_button
        _set_variant(primary, "primary")
        _set_variant(quiet, "secondary")
        self.action_bar.set_actions(primary, (self.cancel_button, quiet))

    # ------------------------------------------------------------------
    # Drag and drop (D50): this screen takes no payload, and says so
    # ------------------------------------------------------------------

    def _drop_refusal(self) -> str:
        """The one reason this screen gives for refusing a dropped payload."""
        return self.tr("Card Backfill works on the selected Anki deck.")

    def _may_answer_a_drop(self) -> bool:
        """Whether the status line is free to carry a drop refusal.

        During a run that line is the only account of what the run is doing, so
        a stray drag must not overwrite ``Scanning…`` with a note about decks.
        The drag is simply not accepted then, and the cursor already says no.
        """
        return self.worker_thread is None

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt override
        """Accept the drag so the refusal can be delivered, and state it now.

        Backfill reads the deck the user picked above; there is no file it could
        take. Accepting only buys the chance to answer -- an ignored drag is the
        silent non-acceptance D50 exists to remove.
        """
        if event is None or not self._may_answer_a_drop():
            return
        if not urls_from_event(event):
            return
        event.acceptProposedAction()
        self.status_label.setText(self._drop_refusal())

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:  # noqa: N802 - Qt override
        """Take the refusal back down when the drag moves off the screen."""
        if self.status_label.text() == self._drop_refusal():
            self.status_label.setText("")
        if event is not None:
            event.accept()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt override
        """Refuse the payload and point at the control that does the choosing."""
        if event is None:
            return
        if self._may_answer_a_drop():
            self.status_label.setText(self._drop_refusal())
            self.deck_combo.setFocus(Qt.FocusReason.OtherFocusReason)
        event.ignore()

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
        self._scan_warnings = ()
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        self._sync_action_prominence()
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
        except ValueError as exc:
            logger.warning(
                "Card Backfill deck fetch skipped: missing=field_mapping fallback=all_decks error=%s",
                exc,
            )
            return  # mapping incomplete; deck filter stays "All decks"
        worker = FetchDecksWorker(service, parent=self)
        worker.result_ready.connect(self._on_decks_fetched)
        worker.error.connect(self._on_deck_fetch_error)
        self._deck_worker = worker
        worker.start()

    def _on_deck_fetch_error(self, message: str) -> None:
        logger.warning(
            "Card Backfill deck fetch degraded: fallback=all_decks error=%s",
            message,
        )
        self._on_decks_fetched([])

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
        self._publish_task_start(self.tr("Card backfill scan"))
        self.status_label.setText(self.tr("Scanning…"))
        logger.info(
            "Card Backfill scan started: field_groups=%d overwrite=%s deck=%s note_type=%s",
            sum(checkbox.isChecked() for checkbox in self.field_checkboxes.values()),
            options.overwrite,
            options.deck or "-",
            self.config.anki_note_type,
        )
        worker.start()

    def _on_scan_finished(self, plan: BackfillPlan, warnings: tuple[str, ...] = ()) -> None:
        self._scan_warnings = tuple(warnings)
        self._plan = plan if plan.notes else None
        self._populate_preview(plan)
        self.apply_button.setEnabled(self._can_apply_plan())
        self._sync_action_prominence()
        self.status_label.setText("")

    def _can_apply_plan(self) -> bool:
        if self._plan is None:
            return False
        if not self._scan_warnings:
            return True
        summary = self.summary_label.text()
        return not self.summary_label.isHidden() and all(warning in summary for warning in self._scan_warnings)

    def _populate_preview(self, plan: BackfillPlan) -> None:
        rows = [(note.expression, change) for note in plan.notes for change in note.changes][:_PREVIEW_ROW_CAP]
        # Sorting has to be off while rows are written, or Qt re-orders under
        # the loop and the cells land on the wrong lines.
        was_sorting = self.preview_table.isSortingEnabled()
        self.preview_table.setSortingEnabled(False)
        try:
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
                    shown = text[:_CELL_ELIDE] + "…" if len(text) > _CELL_ELIDE else text
                    # The cell prints a truncated value; the copy and the sort
                    # key are the whole one. Copying a preview row to check what
                    # is about to be written must not hand back an ellipsis.
                    item = make_table_item(
                        shown,
                        CellRole.TEXT,
                        sort_value=text,
                        copy_text=text,
                        tooltip=text,
                    )
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col == 0:
                        # The Expression is the mined Japanese word; the other three
                        # columns are field names and field contents. Face only, so
                        # the row height stays where the density rule put it.
                        item.setFont(japanese_cell_font())
                    self.preview_table.setItem(row, col, item)
        finally:
            self.preview_table.setSortingEnabled(was_sorting)
        # Re-enabling sorting resets the vertical header's resize mode, which
        # drops the shared fixed row height with it.
        configure_data_view(self.preview_table)
        self.summary_label.setText(self._summary_text(plan, len(rows)))

    @staticmethod
    def _strip_cell(text: str) -> str:
        from anki_miner.services.card_backfiller import _display

        return _display(text)

    def _summary_text(self, plan: BackfillPlan, shown_rows: int) -> str:
        parts: list[str] = list(self._scan_warnings)
        if plan.scanned == 0:
            # A query that matched nothing is NOT "all fields already have
            # values" — that sentence sent users hunting for a filled-in
            # collection when the note type or deck simply didn't match. Name
            # the scope that came back empty instead.
            if plan.options.deck:
                parts.append(
                    self.tr(
                        'No notes matched — note type "{note_type}" in deck "{deck}". Check Settings → Anki.'
                    ).format(note_type=self.config.anki_note_type, deck=plan.options.deck)
                )
            else:
                parts.append(
                    self.tr('No notes matched — note type "{note_type}". Check Settings → Anki.').format(
                        note_type=self.config.anki_note_type
                    )
                )
        elif plan.notes:
            parts.append(
                self.tr("{fields} field(s) across {notes} note(s) will be filled.").format(
                    fields=plan.total_field_changes, notes=len(plan.notes)
                )
            )
            if plan.total_field_changes > shown_rows:
                parts.append(self.tr("Showing first {rows} rows.").format(rows=shown_rows))
        elif not plan.options.overwrite:
            parts.append(self.tr("No new values were found for the selected fields."))
        elif plan.identical_skips > 0:
            parts.append(
                self.tr("Nothing to overwrite — the freshly computed values are identical to the existing content.")
            )
        elif plan.guessed_reading_skips > 0:
            # Not "nothing found": the values existed, they were withheld.
            parts.append(self.tr("Nothing to overwrite — the existing pitch was kept, see below."))
        else:
            # Overwrite scan with zero identical skips: the lookups produced no
            # proposals (word not covered / field absent), so claiming the
            # values are "identical" or "already present" would be false.
            parts.append(self.tr("No new values were found for the selected fields."))
        if plan.identical_skips > 0:
            parts.append(
                self.tr("{count} field value(s) already up to date (identical to the computed value).").format(
                    count=plan.identical_skips
                )
            )
        if plan.guessed_reading_skips > 0:
            parts.append(
                self.tr(
                    "{count} pitch field(s) kept — the reading could only be guessed from the word alone, "
                    "so overwriting could have applied the wrong homograph's accent. "
                    "Map an Expression Reading or Furigana field to overwrite them."
                ).format(count=plan.guessed_reading_skips)
            )
        if plan.absent_fields:
            # Distinct from unavailable_fields (resource not loaded): the field
            # name itself is not on the note type, so the mapping is stale and
            # no amount of installing dictionaries will help.
            parts.append(
                self.tr(
                    "Not on this note type (stale mapping): {fields}. Fix in Settings → Anki field mapping."
                ).format(fields=", ".join(plan.absent_fields))
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
            self._scan_warnings = ()
            self.preview_table.setRowCount(0)
            self.apply_button.setEnabled(False)
            self.summary_label.setText("")
            self._sync_action_prominence()
            self.status_label.setText(self.tr("Settings changed since this scan; re-scan before applying."))
            return
        answer = QMessageBox.question(
            self,
            self.tr("Update notes in Anki?"),
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
        worker.cancelled.connect(self._on_apply_cancelled)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        self._set_running(True)
        self._publish_task_start(self.tr("Card backfill"), total=len(plan.notes))
        self.status_label.setText(self.tr("Applying…"))
        logger.info(
            "Card Backfill apply started: field_groups=%d overwrite=%s deck=%s note_type=%s",
            sum(
                any(field_key in plan.options.field_keys for field_key in group_keys)
                for group_keys in FIELD_GROUPS.values()
            ),
            plan.options.overwrite,
            plan.options.deck or "-",
            self.config.anki_note_type,
        )
        worker.start()

    def _on_apply_finished(self, result: BackfillResult) -> None:
        self._plan = None
        self._scan_warnings = ()
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        parts = [
            self.tr("Filled {fields} field(s) on {notes} note(s).").format(
                fields=result.fields_filled,
                notes=result.notes_updated,
            )
        ]
        if result.tagged:
            parts.append(self.tr("Tagged {tag}.").format(tag=BACKFILL_TAG))
        if result.skipped_stale:
            parts.append(
                self.tr("{count} skipped — changed or deleted since the scan.").format(count=result.skipped_stale)
            )
        if result.tagged < result.notes_updated:
            parts.append(self.tr("Tagging failed for some notes (see log)."))
        if result.failed:
            parts.append(
                self.tr("{count} note update(s) were not confirmed by Anki; scan again to retry.").format(
                    count=result.failed
                )
            )
            self._run_failed = True
        self.status_label.setText(" ".join(parts))

    def _on_apply_cancelled(self) -> None:
        self._plan = None
        self._scan_warnings = ()
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        receipt = self.status_label.text()
        if receipt in {self.tr("Applying…"), self.tr("Cancelling…")}:
            self.status_label.setText(self.tr("Cancelled."))
        elif not receipt.startswith(self.tr("Cancelled.")):
            self.status_label.setText(f"{self.tr('Cancelled.')} {receipt}")

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.apply_button.setEnabled(not running and self._can_apply_plan())
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
        self._sync_action_prominence()

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._cancel()

    def _cancel(self) -> None:
        """Cancel the run: one verb, no prompt, and the button says it is waiting."""
        if self.worker_thread is not None and self.worker_thread.isRunning():
            self._publish_task_cancelling()
            self.worker_thread.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText(self.tr("Cancelling…"))

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
        self._publish_task_count(current=done, total=total or None, detail="")

    def _on_worker_error(self, message: str) -> None:
        logger.warning("Card Backfill worker failed: error=%s", message)
        self._run_failed = True
        self._set_running(False)
        self.status_label.setText(message)

    def _on_worker_finished(self) -> None:
        self._set_running(False)
        cancelled = self.worker_thread is not None and self.worker_thread.is_cancelled
        self._publish_task_finish(self._task_outcome(cancelled=cancelled, failed=self._run_failed))
        self._run_failed = False
        self.worker_thread = None

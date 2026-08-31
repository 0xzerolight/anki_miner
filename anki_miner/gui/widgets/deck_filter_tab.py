"""Deck Filter tool tab (Utilities → Deck Filter).

Filters a premade Anki deck through the app's word filters and copies the
kept notes into a NEW deck; the source deck is never modified. Two-step
flow mirroring Card Backfill: Scan (read-only, off-thread) builds a
:class:`DeckFilterPlan` shown as a summary + preview table; Copy creates the
target deck and copies exactly the previewed notes, tagging them
``anki-miner::deckfilter``.

Plain ``QWidget`` (not ``_ToolTabBase`` — that base is file-processing
chrome). The active worker lives on ``self.worker_thread``,
``iter_close_workers()`` yields it for the app-close join, and
``update_config`` drops any held plan (its filter decisions are
config-stale).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from anki_miner.gui.utils.content_text import content_cell_font
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
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets.base import (
    PageWidth,
    TaskPublisherMixin,
    capped_page_column,
    install_workflow_shell,
)
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.enhanced.modern_button import ButtonVariant
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.deck_filter_worker import DeckFilterApplyWorker, DeckFilterScanWorker
from anki_miner.gui.workers.fetch_workers import FetchDecksWorker
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.deck_filter import (
    DECKFILTER_TAG,
    DeckFilterOptions,
    DeckFilterPlan,
    DeckFilterResult,
    DeckInspection,
    inspect_deck,
)

logger = logging.getLogger(__name__)

_PREVIEW_ROW_CAP = 500
_CELL_ELIDE = 120
#: Same whole-row floor rationale as Card Backfill (Issue #102 class).
PREVIEW_MIN_VISIBLE_ROWS = 8


def _set_variant(button: ModernButton, variant: ButtonVariant) -> None:
    """Re-role a button in place (unpolish/polish; see backfill_tab)."""
    if button.objectName() == variant:
        return
    button.setObjectName(variant)
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)


class DeckFilterTab(TaskPublisherMixin, QWidget):
    """Scan → summary + preview table → Copy into a new deck."""

    #: The preview table genuinely uses the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    #: Scan and Copy are two runs of the same screen; one id, they supersede.
    TASK_ID = "tools.deckfilter"
    TASK_OWNER = CapabilityTarget("subtitles", "deckfilter")

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        # The Expression column is mined content, so its face follows the mining
        # language; re-derived in update_config when the language changes.
        self._content_style = get_profile(config_language(config)).content_style
        self.worker_thread: DeckFilterScanWorker | DeckFilterApplyWorker | None = None
        self._plan: DeckFilterPlan | None = None
        self._scan_warnings: tuple[str, ...] = ()
        self._decks_requested = False
        self._deck_worker: SingleCallWorker | None = None
        self._inspect_worker: SingleCallWorker | None = None
        #: Monotonic guard: an inspect result for a deck the user has since
        #: navigated away from must not repopulate the field pickers.
        self._inspect_generation = 0
        self._last_auto_deck_name = ""
        self._run_failed = False
        self._build_ui()
        # Drops are answered, not swallowed (D50).
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(SectionHeader(self.tr("Deck Filter")))
        hint = QLabel(
            self.tr(
                "Copy the worth-learning part of a premade deck into a new deck. "
                "Notes are kept or dropped by your filters — known words, frequency "
                "band, blacklist, script type and name wordsets (Settings → Filtering). "
                "The source deck is not modified."
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("Source deck:")))
        self.source_combo = QComboBox()
        self.source_combo.addItem(self.tr("Select a deck…"))
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_combo, stretch=1)
        layout.addLayout(source_row)

        fields_row = QHBoxLayout()
        fields_row.addWidget(QLabel(self.tr("Word field:")))
        self.expression_combo = QComboBox()
        self.expression_combo.addItem(self.tr("(first field)"))
        fields_row.addWidget(self.expression_combo, stretch=1)
        fields_row.addWidget(QLabel(self.tr("Reading field:")))
        self.reading_combo = QComboBox()
        self.reading_combo.addItem(self.tr("(none — generate)"))
        fields_row.addWidget(self.reading_combo, stretch=1)
        layout.addLayout(fields_row)
        self._set_field_combos_enabled(False)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(self.tr("New deck:")))
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText(self.tr("Name for the filtered deck"))
        target_row.addWidget(self.target_edit, stretch=1)
        layout.addLayout(target_row)

        self.filters_label = QLabel("")
        self.filters_label.setWordWrap(True)
        layout.addWidget(self.filters_label)
        self._refresh_filters_summary()

        self.scan_button = ModernButton(self.tr("Scan deck (read-only)"), variant="primary")
        self.scan_button.clicked.connect(self._start_scan)
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.preview_table = QTableWidget(0, 3)
        self.preview_table.setHorizontalHeaderLabels([self.tr("Expression"), self.tr("Reading"), self.tr("Freq. rank")])
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.preview_table.setSortingEnabled(True)
        configure_data_view(self.preview_table)
        install_copy_rows(self.preview_table)
        header = self.preview_table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
            # Preview opens in plan order (the copy order); sort only on ask.
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._apply_preview_height_floor()
        layout.addWidget(self.preview_table, stretch=1)

        self.apply_button = ModernButton(self.tr("Copy Notes to New Deck"), variant="secondary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._start_apply)

        install_no_scroll_on_inputs(container)

        scroll_area = QScrollArea()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # No activity log; Activity stays hidden rather than opening empty.
        self.action_bar = install_workflow_shell(outer, scroll_area, container, self.PAGE_WIDTH, log=None)
        outer.insertWidget(outer.count() - 1, capped_page_column(self._create_run_status(), self.PAGE_WIDTH))
        self._sync_action_prominence()
        # Ctrl+Enter runs whichever verb the stage is showing (D48-B).
        primary_action_shortcut(self, self.action_bar.trigger_primary)

    def _apply_preview_height_floor(self) -> None:
        header = self.preview_table.horizontalHeader()
        header_h = header.sizeHint().height() if header is not None else 0
        frame = 2 * self.preview_table.frameWidth()
        self.preview_table.setMinimumHeight(
            header_h + frame + PREVIEW_MIN_VISIBLE_ROWS * data_row_height(self.preview_table)
        )

    def changeEvent(self, a0) -> None:  # noqa: N802 - Qt override
        """Re-derive the preview's row metrics when the UI text size changes."""
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
        """Scan is primary until a plan exists; then Copy takes over."""
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
        return self.tr("Deck Filter works on a deck already in Anki — pick it above.")

    def _may_answer_a_drop(self) -> bool:
        return self.worker_thread is None

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None or not self._may_answer_a_drop():
            return
        if not urls_from_event(event):
            return
        event.acceptProposedAction()
        self.status_label.setText(self._drop_refusal())

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:  # noqa: N802 - Qt override
        if self.status_label.text() == self._drop_refusal():
            self.status_label.setText("")
        if event is not None:
            event.accept()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None:
            return
        if self._may_answer_a_drop():
            self.status_label.setText(self._drop_refusal())
            self.source_combo.setFocus(Qt.FocusReason.OtherFocusReason)
        event.ignore()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _refresh_filters_summary(self) -> None:
        """Say which filters the next scan will actually apply.

        The scan reads the same Settings → Filtering config as mining; this
        line keeps the screen honest about what "your filters" means today.
        """
        active: list[str] = []
        active.append(self.tr("known words"))
        if self.config.min_frequency_rank > 0 or self.config.max_frequency_rank > 0:
            active.append(self.tr("frequency band"))
        if self.config.use_blacklist:
            active.append(self.tr("blacklist"))
        if self.config.use_whitelist:
            active.append(self.tr("whitelist (force-include)"))
        if self.config.exclude_hiragana_only_words or self.config.exclude_katakana_only_words:
            active.append(self.tr("script type"))
        if self.config.excluded_wordsets:
            active.append(self.tr("name wordsets"))
        self.filters_label.setText(self.tr("Active filters: {filters}.").format(filters=", ".join(active)))

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new config: drop any held plan (its decisions are stale)."""
        self.config = config
        self._content_style = get_profile(config_language(config)).content_style
        self._plan = None
        self._scan_warnings = ()
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        self._sync_action_prominence()
        self._refresh_filters_summary()

    def iter_close_workers(self) -> Iterator[DeckFilterScanWorker | DeckFilterApplyWorker | SingleCallWorker]:
        # Both lazy fetches run blocking AnkiConnect calls; abandoning them to
        # Qt teardown aborts with "QThread: Destroyed while thread is still
        # running", so surface them for the close-join policy alongside the run
        # worker.
        #
        # ``still_running``, never a raw ``isRunning()``: ``_inspect_worker``
        # comes from ``run_off_thread``, which owns the worker's lifetime and
        # deleteLater()s it on finish. The handle is not cleared, so once an
        # inspect completes it is a live Python wrapper around a destroyed C++
        # object and ``isRunning()`` raises RuntimeError -- out of closeEvent,
        # into the excepthook dialog, and past the config save at the end of
        # MainWindow.closeEvent.
        for worker in (self.worker_thread, self._deck_worker, self._inspect_worker):
            if still_running(worker):
                assert worker is not None
                yield worker

    # ------------------------------------------------------------------
    # Deck dropdown (lazy fetch on first show) + source inspection
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
            logger.warning("Deck Filter deck fetch skipped: missing=field_mapping error=%s", exc)
            return
        worker = FetchDecksWorker(service, parent=self)
        worker.result_ready.connect(self._on_decks_fetched)
        worker.error.connect(self._on_deck_fetch_error)
        self._deck_worker = worker
        worker.start()

    def _on_deck_fetch_error(self, message: str) -> None:
        logger.warning("Deck Filter deck fetch failed: error=%s", message)
        self._on_decks_fetched([])

    def _on_decks_fetched(self, decks: list) -> None:
        if decks:
            self.source_combo.addItems([str(d) for d in decks])
        else:
            self.status_label.setText(self.tr("Couldn't fetch deck names from Anki — is Anki running?"))

    def _selected_source_deck(self) -> str | None:
        return self.source_combo.currentText() if self.source_combo.currentIndex() > 0 else None

    def _on_source_changed(self, _index: int) -> None:
        deck = self._selected_source_deck()
        self._reset_field_combos()
        if deck is None:
            return
        # Suggest a target name, but never fight a name the user typed.
        suggestion = self.tr("{deck} (Filtered)").format(deck=deck)
        if not self.target_edit.text().strip() or self.target_edit.text() == self._last_auto_deck_name:
            self.target_edit.setText(suggestion)
        self._last_auto_deck_name = suggestion
        self._start_inspect(deck)

    def _reset_field_combos(self) -> None:
        for combo in (self.expression_combo, self.reading_combo):
            while combo.count() > 1:
                combo.removeItem(combo.count() - 1)
            combo.setCurrentIndex(0)
        self._set_field_combos_enabled(False)

    def _set_field_combos_enabled(self, enabled: bool) -> None:
        self.expression_combo.setEnabled(enabled)
        self.reading_combo.setEnabled(enabled)

    def _start_inspect(self, deck: str) -> None:
        try:
            service = AnkiService(self.config)
        except ValueError as exc:
            logger.warning("Deck Filter inspect skipped: missing=field_mapping error=%s", exc)
            return
        self._inspect_generation += 1
        generation = self._inspect_generation
        self._inspect_worker = run_off_thread(
            self,
            lambda: inspect_deck(service, deck),
            lambda inspection: self._on_inspected(generation, inspection),
            lambda message: self._on_inspect_error(generation, message),
            error_prefix=self.tr("Couldn't read the deck: "),
        )

    def _on_inspected(self, generation: int, inspection: object) -> None:
        if generation != self._inspect_generation or not isinstance(inspection, DeckInspection):
            return
        for name in inspection.field_names:
            self.expression_combo.addItem(name)
            self.reading_combo.addItem(name)
        self._set_field_combos_enabled(bool(inspection.field_names))
        if inspection.note_count == 0:
            self.status_label.setText(self.tr("The selected deck has no notes."))
        else:
            self.status_label.setText(self.tr("{count} note(s) in the deck.").format(count=inspection.note_count))

    def _on_inspect_error(self, generation: int, message: str) -> None:
        if generation != self._inspect_generation:
            return
        logger.warning("Deck Filter inspect failed: error=%s", message)
        self.status_label.setText(message)

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _combo_field(self, combo: QComboBox) -> str | None:
        return combo.currentText() if combo.currentIndex() > 0 else None

    def _build_options(self) -> DeckFilterOptions | None:
        source = self._selected_source_deck()
        if source is None:
            self.status_label.setText(self.tr("Pick the source deck first."))
            return None
        target = self.target_edit.text().strip()
        if not target:
            self.status_label.setText(self.tr("Name the new deck first."))
            return None
        if target == source:
            self.status_label.setText(self.tr("The new deck needs a different name than the source deck."))
            return None
        return DeckFilterOptions(
            source_deck=source,
            target_deck=target,
            expression_field=self._combo_field(self.expression_combo),
            reading_field=self._combo_field(self.reading_combo),
        )

    def _start_scan(self) -> None:
        options = self._build_options()
        if options is None:
            return
        worker = DeckFilterScanWorker(self.config, options, parent=self)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_scan_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        self._set_running(True)
        self._publish_task_start(self.tr("Deck filter scan"))
        self.status_label.setText(self.tr("Scanning…"))
        logger.info(
            "Deck Filter scan started: source=%s expression_field=%s",
            options.source_deck,
            options.expression_field or "-",
        )
        worker.start()

    def _on_scan_finished(self, plan: DeckFilterPlan, warnings: tuple[str, ...] = ()) -> None:
        self._scan_warnings = tuple(warnings)
        self._plan = plan if plan.kept else None
        self._populate_preview(plan)
        self.apply_button.setEnabled(self._plan is not None)
        self._sync_action_prominence()
        self.status_label.setText("")

    def _drop_reason_labels(self) -> dict[str, str]:
        return {
            "no_expression": self.tr("empty word field"),
            "not_japanese": self.tr("not the mining language"),
            "duplicate_in_source": self.tr("duplicate within the deck"),
            "known": self.tr("already known or carded"),
            "unranked": self.tr("no frequency rank"),
            "frequency_band": self.tr("outside the frequency band"),
            "blacklist": self.tr("blacklisted"),
            "script_type": self.tr("script type"),
            "name_wordset": self.tr("name (wordset)"),
        }

    def _summary_text(self, plan: DeckFilterPlan, shown_rows: int) -> str:
        parts: list[str] = list(self._scan_warnings)
        if plan.scanned == 0:
            parts.append(self.tr('No notes found in deck "{deck}".').format(deck=plan.options.source_deck))
        else:
            parts.append(
                self.tr("{kept} of {scanned} note(s) will be copied.").format(kept=len(plan.kept), scanned=plan.scanned)
            )
            labels = self._drop_reason_labels()
            dropped = ", ".join(f"{labels.get(reason, reason)}: {count}" for reason, count in plan.drops)
            if dropped:
                parts.append(self.tr("Dropped — {reasons}.").format(reasons=dropped))
            if plan.forced_count:
                parts.append(self.tr("{count} kept by whitelist.").format(count=plan.forced_count))
            if len(plan.kept) > shown_rows:
                parts.append(self.tr("Showing first {rows} rows.").format(rows=shown_rows))
        return " ".join(parts)

    def _populate_preview(self, plan: DeckFilterPlan) -> None:
        rows = plan.kept[:_PREVIEW_ROW_CAP]
        was_sorting = self.preview_table.isSortingEnabled()
        self.preview_table.setSortingEnabled(False)
        try:
            self.preview_table.setRowCount(len(rows))
            for row, kept in enumerate(rows):
                rank = str(kept.frequency_rank) if kept.frequency_rank is not None else ""
                for col, (text, role) in enumerate(
                    ((kept.expression, CellRole.TEXT), (kept.reading, CellRole.TEXT), (rank, CellRole.NUMBER))
                ):
                    shown = text[:_CELL_ELIDE] + "…" if len(text) > _CELL_ELIDE else text
                    sort_value: str | int = text
                    if role is CellRole.NUMBER:
                        sort_value = kept.frequency_rank if kept.frequency_rank is not None else 10**9
                    item = make_table_item(
                        shown,
                        role,
                        sort_value=sort_value,
                        copy_text=text,
                        tooltip=text,
                    )
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col == 0:
                        item.setFont(content_cell_font(self._content_style))
                    self.preview_table.setItem(row, col, item)
        finally:
            self.preview_table.setSortingEnabled(was_sorting)
        configure_data_view(self.preview_table)
        self.summary_label.setText(self._summary_text(plan, len(rows)))

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
            self.status_label.setText(self.tr("Settings changed since this scan; re-scan before copying."))
            return
        answer = QMessageBox.question(
            self,
            self.tr("Copy notes to a new deck?"),
            self.tr(
                'This will create deck "{deck}" and copy {notes} note(s) into it, '
                "tagged {tag}. The source deck is not modified. Continue?"
            ).format(deck=plan.options.target_deck, notes=len(plan.kept), tag=DECKFILTER_TAG),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = DeckFilterApplyWorker(self.config, plan, parent=self)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_apply_finished)
        worker.cancelled.connect(self._on_apply_cancelled)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        self._set_running(True)
        self._publish_task_start(self.tr("Deck filter copy"), total=len(plan.kept))
        self.status_label.setText(self.tr("Copying…"))
        logger.info(
            "Deck Filter apply started: target=%s notes=%d",
            plan.options.target_deck,
            len(plan.kept),
        )
        worker.start()

    def _on_apply_finished(self, result: DeckFilterResult) -> None:
        target = self._plan.options.target_deck if self._plan is not None else ""
        self._plan = None
        self._scan_warnings = ()
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")
        parts = [self.tr('Copied {count} note(s) into "{deck}".').format(count=result.created, deck=target)]
        if result.not_created:
            parts.append(
                self.tr("{count} note(s) were not accepted by Anki (see log).").format(count=result.not_created)
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
        if receipt in {self.tr("Copying…"), self.tr("Cancelling…")}:
            self.status_label.setText(self.tr("Cancelled."))
        elif not receipt.startswith(self.tr("Cancelled.")):
            self.status_label.setText(f"{self.tr('Cancelled.')} {receipt}")

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.apply_button.setEnabled(not running and self._plan is not None)
        self.cancel_button.setEnabled(running)
        self.source_combo.setEnabled(not running)
        self.target_edit.setEnabled(not running)
        if running:
            self._set_field_combos_enabled(False)
        else:
            self._set_field_combos_enabled(self.expression_combo.count() > 1)
        self.progress_bar.setVisible(running)
        if running:
            self.progress_bar.setRange(0, 0)
        self._sync_action_prominence()

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._cancel()

    def _cancel(self) -> None:
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
        logger.warning("Deck Filter worker failed: error=%s", message)
        self._run_failed = True
        self._set_running(False)
        self.status_label.setText(message)

    def _on_worker_finished(self) -> None:
        self._set_running(False)
        cancelled = self.worker_thread is not None and self.worker_thread.is_cancelled
        self._publish_task_finish(self._task_outcome(cancelled=cancelled, failed=self._run_failed))
        self._run_failed = False
        self.worker_thread = None

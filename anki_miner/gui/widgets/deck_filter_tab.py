"""Deck Filter tool tab (Utilities → Deck Filter).

Filters a premade Anki deck through the app's word filters and copies the
kept notes into a NEW deck; the source deck is never modified. Two-step
flow mirroring Card Backfill: Scan (read-only, off-thread) builds a
:class:`DeckFilterPlan` shown as a summary + preview table; Copy creates the
target deck and copies exactly the previewed notes, tagging them
``anki-miner::deckfilter``.

Built on ``_AnkiPlanTabBase`` with Card Backfill (not ``_ToolTabBase`` — that
base is file-processing chrome). The active worker lives on
``self.worker_thread``, ``iter_close_workers()`` yields it for the app-close
join, and ``update_config`` drops any held plan (its filter decisions are
config-stale).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
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
    install_copy_rows,
    install_no_scroll_on_inputs,
    make_table_item,
)
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets._anki_plan_tab_base import _AnkiPlanTabBase, _PlanTabStrings
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    configure_card_layout,
    install_workflow_shell,
    page_filler,
)
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
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
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

_PREVIEW_ROW_CAP = 500
_CELL_ELIDE = 120


class DeckFilterTab(_AnkiPlanTabBase):
    """Scan → summary + preview table → Copy into a new deck."""

    #: The preview table genuinely uses the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    #: Scan and Copy are two runs of the same screen; one id, they supersede.
    TASK_ID = "tools.deckfilter"
    TASK_OWNER = CapabilityTarget("subtitles", "deckfilter")

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        # Built here, not in the base, so every literal keeps this tab's
        # tr-context (see _anki_plan_tab_base).
        self._strings = _PlanTabStrings(
            applying=self.tr("Copying…"),
            cancelling=self.tr("Cancelling…"),
            cancelled=self.tr("Cancelled."),
            settings_changed=self.tr("Settings changed since this scan; re-scan before copying."),
            couldnt_fetch_decks=self.tr("Couldn't fetch deck names from Anki — is Anki running?"),
            worker_failed=self.tr("Deck Filter could not finish."),
        )
        # The Expression column is mined content, so its face follows the mining
        # language; re-derived in update_config when the language changes.
        self._content_style = get_profile(config_language(config)).content_style
        self.worker_thread: DeckFilterScanWorker | DeckFilterApplyWorker | None = None
        self._plan: DeckFilterPlan | None = None
        self._scan_warnings: tuple[str, ...] = ()
        # "Did a deck list arrive?", NOT "did we ask?" — see ensure_decks.
        self._decks_loaded = False
        #: True while the banner may carry the deck-fetch failure, so a later
        #: success clears that banner and nothing else shown since.
        self._deck_fetch_failed = False
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
        layout.setSpacing(SPACING.sm)

        # E07: cards like every other tool, and no page heading repeating the
        # tab label.
        self.deck_card = QFrame()
        self.deck_card.setObjectName("card")
        deck_layout = QVBoxLayout(self.deck_card)
        configure_card_layout(deck_layout)
        deck_layout.addWidget(SectionHeader(self.tr("Deck")))
        hint = QLabel(
            self.tr(
                "Copy the worth-learning part of a premade deck into a new deck. "
                "Filters come from Settings → Word Filters; the source deck is not modified."
            )
        )
        hint.setObjectName("helper-text")
        hint.setWordWrap(True)
        deck_layout.addWidget(hint)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("Source deck:")))
        self.source_combo = QComboBox()
        self.source_combo.addItem(self.tr("Select a deck…"))
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_combo, stretch=1)
        deck_layout.addLayout(source_row)

        # What the chosen deck holds, right under it (was on the run line).
        self.deck_info_label = QLabel("")
        self.deck_info_label.setObjectName("row-meta")
        self.deck_info_label.hide()
        deck_layout.addWidget(self.deck_info_label)

        # E07: the field pickers mean nothing until the deck is read.
        self.field_row = QWidget()
        fields_row = QHBoxLayout(self.field_row)
        fields_row.setContentsMargins(0, 0, 0, 0)
        fields_row.addWidget(QLabel(self.tr("Word field:")))
        self.expression_combo = QComboBox()
        self.expression_combo.addItem(self.tr("(first field)"))
        fields_row.addWidget(self.expression_combo, stretch=1)
        fields_row.addWidget(QLabel(self.tr("Reading field:")))
        self.reading_combo = QComboBox()
        self.reading_combo.addItem(self.tr("(none — generate)"))
        fields_row.addWidget(self.reading_combo, stretch=1)
        self.field_row.hide()
        deck_layout.addWidget(self.field_row)
        self._set_field_combos_enabled(False)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(self.tr("New deck:")))
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText(self.tr("Name for the filtered deck"))
        target_row.addWidget(self.target_edit, stretch=1)
        deck_layout.addLayout(target_row)
        layout.addWidget(self.deck_card)

        self.filters_card = QFrame()
        self.filters_card.setObjectName("card")
        filters_layout = QVBoxLayout(self.filters_card)
        configure_card_layout(filters_layout)
        filters_layout.addWidget(SectionHeader(self.tr("Filters")))
        self.filters_label = QLabel("")
        self.filters_label.setWordWrap(True)
        filters_layout.addWidget(self.filters_label)
        layout.addWidget(self.filters_card)
        self._refresh_filters_summary()

        self.scan_button = ModernButton(self.tr("Scan deck (read-only)"), variant="primary")
        self.scan_button.clicked.connect(self._start_scan)
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.hide()  # shown only while a scan or copy runs (E07)
        self.cancel_button.clicked.connect(self._cancel)

        self.preview_card = QFrame()
        self.preview_card.setObjectName("card")
        preview_layout = QVBoxLayout(self.preview_card)
        configure_card_layout(preview_layout)
        preview_layout.addWidget(SectionHeader(self.tr("Preview")))

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        preview_layout.addWidget(self.summary_label)

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
        self.preview_table.hide()
        preview_layout.addWidget(self.preview_table, stretch=1)
        self.preview_card.hide()
        # No static stretch: the card grows through its table while the table
        # shows, and the filler takes the surplus while it does not, so a tall
        # window never pads the card headings.
        layout.addWidget(self.preview_card)
        self.page_filler = page_filler()
        layout.addWidget(self.page_filler)

        self.apply_button = ModernButton(self.tr("Copy Notes to New Deck"), variant="secondary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._start_apply)

        install_no_scroll_on_inputs(container)

        scroll_area = QScrollArea()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # No activity log; Activity stays hidden rather than opening empty.
        self.action_bar = install_workflow_shell(outer, scroll_area, container, self.PAGE_WIDTH, log=None)
        # D1: the run line folds into the pinned bar. The strip stays built as
        # the line's model (status_label, progress_bar) but is never shown.
        strip = self._create_run_status()
        strip.setParent(self)
        strip.hide()
        self.action_bar.set_keeps_last_result(True)
        self.install_issue_banner(outer)
        self._sync_action_prominence()
        # Ctrl+Enter runs whichever verb the stage is showing (D48-B).
        primary_action_shortcut(self, self.action_bar.trigger_primary)

    # ------------------------------------------------------------------
    # Drag and drop (D50): this screen takes no payload, and says so
    # ------------------------------------------------------------------

    def _drop_refusal(self) -> str:
        """The one reason this screen gives for refusing a dropped payload."""
        return self.tr("Deck Filter works on a deck already in Anki — pick it above.")

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _refresh_filters_summary(self) -> None:
        """Say which filters the next scan will actually apply.

        The scan reads the same Settings → Word Filters config as mining; this
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
        self._drop_plan()
        self._sync_action_prominence()
        self._refresh_filters_summary()

    def _extra_close_workers(self) -> tuple[SingleCallWorker | None, ...]:
        # The source-deck inspection: ``run_off_thread`` owns its lifetime and
        # deleteLater()s it on finish without clearing this handle, which is why
        # the base guards every handle with ``still_running``.
        return (self._inspect_worker,)

    # ------------------------------------------------------------------
    # Deck dropdown (lazy fetch on first show) + source inspection
    # ------------------------------------------------------------------

    def _deck_combo(self) -> QComboBox:
        return self.source_combo

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

    def _selected_source_deck(self) -> str | None:
        return self.source_combo.currentText() if self.source_combo.currentIndex() > 0 else None

    def _on_source_changed(self, _index: int) -> None:
        # A new source choice supersedes any complaint about the old one (or
        # about there being none). The deck-fetch banner cannot be up here: it
        # clears on the fetch that fills this combo.
        self.clear_screen_issue()
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
        self.field_row.hide()
        self.deck_info_label.hide()

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
        self.field_row.setVisible(bool(inspection.field_names))
        if inspection.note_count == 0:
            self.deck_info_label.setText(self.tr("The selected deck has no notes."))
        else:
            self.deck_info_label.setText(self.tr("%n note(s) in the deck.", "", inspection.note_count))
        self.deck_info_label.show()

    def _on_inspect_error(self, generation: int, message: str) -> None:
        if generation != self._inspect_generation:
            return
        logger.warning("Deck Filter inspect failed: error=%s", message)
        self.show_screen_issue(ScreenIssue(summary=self.tr("The deck could not be read."), details=message))

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _combo_field(self, combo: QComboBox) -> str | None:
        return combo.currentText() if combo.currentIndex() > 0 else None

    def _build_options(self) -> DeckFilterOptions | None:
        source = self._selected_source_deck()
        if source is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Pick the source deck first.")))
            return None
        target = self.target_edit.text().strip()
        if not target:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Name the new deck first.")))
            return None
        if target.casefold() == source.casefold():
            # Anki resolves deck names ignoring case, so "core 2k" IS "Core 2k" (BA-022).
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("The new deck needs a different name than the source deck."))
            )
            return None
        return DeckFilterOptions(
            source_deck=source,
            target_deck=target,
            expression_field=self._combo_field(self.expression_combo),
            reading_field=self._combo_field(self.reading_combo),
        )

    def _start_scan(self) -> None:
        # A fresh attempt supersedes the complaint about the last one (D24).
        self.clear_screen_issue()
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
        self._set_run_line(self.tr("Scanning…"))
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
        self.preview_card.show()
        has_rows = self.preview_table.rowCount() > 0
        self.preview_table.setVisible(has_rows)
        self.page_filler.setVisible(not has_rows)
        self.apply_button.setEnabled(self._plan is not None)
        self._sync_action_prominence()
        self._set_run_line("")

    def _drop_reason_labels(self) -> dict[str, str]:
        return {
            "no_expression": self.tr("empty word field"),
            "not_japanese": self.tr("not the mining language"),
            "duplicate_in_source": self.tr("duplicate within the deck"),
            "known": self.tr("already known or in Anki"),
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
            parts.append(tr_format(self.tr("%1 of %n note(s) will be copied.", "", plan.scanned), len(plan.kept)))
            labels = self._drop_reason_labels()
            dropped = ", ".join(f"{labels.get(reason, reason)}: {count}" for reason, count in plan.drops)
            if dropped:
                parts.append(self.tr("Dropped — {reasons}.").format(reasons=dropped))
            if plan.forced_count:
                parts.append(self.tr("%n kept by whitelist.", "", plan.forced_count))
            if len(plan.kept) > shown_rows:
                parts.append(self.tr("Showing first %n row(s).", "", shown_rows))
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
        if self._drop_stale_plan(plan.config_version):
            return
        answer = QMessageBox.question(
            self,
            self.tr("Copy notes to a new deck?"),
            tr_format(
                self.tr(
                    'This will create deck "%1" and copy %n note(s) into it, '
                    "tagged %2. The source deck is not modified. Continue?",
                    "",
                    len(plan.kept),
                ),
                plan.options.target_deck,
                DECKFILTER_TAG,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Apply is a run entry: a failed Apply's banner must not outlive the retry.
        self.clear_screen_issue()
        worker = DeckFilterApplyWorker(self.config, plan, parent=self)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_apply_finished)
        worker.cancelled.connect(self._on_apply_cancelled)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker
        self._set_running(True)
        self._publish_task_start(self.tr("Deck filter copy"), total=len(plan.kept))
        self._set_run_line(self._strings.applying)
        logger.info(
            "Deck Filter apply started: target=%s notes=%d",
            plan.options.target_deck,
            len(plan.kept),
        )
        worker.start()

    def _on_apply_finished(self, result: DeckFilterResult) -> None:
        target = self._plan.options.target_deck if self._plan is not None else ""
        self._drop_plan()
        parts = [tr_format(self.tr('Copied %n note(s) into "%1".', "", result.created), target)]
        if result.not_created:
            parts.append(self.tr("%n note(s) were not accepted by Anki (see log).", "", result.not_created))
            self._run_failed = True
        self._set_run_line(" ".join(parts))

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.apply_button.setEnabled(not running and self._plan is not None)
        self.cancel_button.setVisible(running)
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

    def _on_worker_error(self, message: str) -> None:
        logger.warning("Deck Filter worker failed: error=%s", message)
        self._run_failed = True
        self._set_running(False)
        self._set_run_line("")
        self.show_screen_issue(ScreenIssue(summary=self._strings.worker_failed, details=message))

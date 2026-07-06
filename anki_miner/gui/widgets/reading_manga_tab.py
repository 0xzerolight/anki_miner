"""Manga sub-tab of the Reading tab: quick-folder mining plus a volume queue.

Two ways in, one shared worker/processor lifecycle (owned by
:class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`):

* **Quick Processing card** — a folder selector plus Preview / Mine. The folder
  is classified by ``detector.detect``: a single-volume folder mines straight
  away as an ephemeral item (never added to the queue); a series folder of many
  volumes expands into queue rows instead and waits for *Process Queue*.
* **Manga queue card** — *Add Series Folder…* / *Add Volumes…* (or a drag-drop
  of folders / ``.mokuro`` / ``.cbz`` / ``.zip``) build a list of
  :class:`ReadingQueueItemWidget` rows that *Process Queue* mines together.

Progress is shown on two bars: overall (item N of M) and current (the mining
stage sweep of the active item).

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they refresh
the row display and the summary bars, never write status/cards/error. A queued
``item_started`` slot arriving late must not overwrite a COMPLETED status back
to PROCESSING.

Button enable/disable is recomputed on every queue/worker change by
:meth:`_recompute_buttons`. There is no explicit state flag — the queue
contents plus the worker handle fully determine the UI.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.exceptions import SetupError
from anki_miner.gui.constants import MIN_HEIGHT_QUEUE_SECTION
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.base import field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.reading_queue_item_widget import ReadingQueueItemWidget
from anki_miner.models.reading_queue import ReadingItemStatus, ReadingQueue, ReadingQueueItem
from anki_miner.services.reading import detector
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)

# File-dialog filter glob for the "Add Volumes…" button. Multi-select so a user
# can pick every volume in a title folder at once (each is expanded by detect).
# The human label ("Manga") is tr()'d at call time; only the literal extension
# glob lives here.
_MANGA_FILTER_GLOB = "*.mokuro *.cbz *.zip"

# Extensions accepted from a drag-drop / a folder (dirs are always accepted).
# Manga sources feed _add_source_path; novel drops earn a cross-tab hint.
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")
_NOVEL_EXTS = (".epub", ".txt")


class ReadingMangaTab(_ReadingMiningTabBase):
    """Quick-folder + queue manga mining sub-tab.

    Owns a :class:`ReadingQueue` and, via the base, at most one running
    :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`.
    Button state is derived from the queue contents and the worker handle by
    :meth:`_recompute_buttons`.

    Reading curation is table-only (D8): the base inherits the ``(None, None)``
    curation context — this tab does NOT override ``_build_curation_context``.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the manga sub-tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None`` so the tab can be constructed before the
                dictionary chain has loaded; the first run builds one lazily.
            presenter: Optional presenter for routing results.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy
                processor rebuilds so reading mining sessions land in analytics.
        """
        super().__init__(config, processor, presenter, parent, stats_service)

        # Queue model + per-row widget map.
        self._queue: ReadingQueue = ReadingQueue()
        self._row_widgets: dict[ReadingQueueItem, ReadingQueueItemWidget] = {}
        self._list_items: dict[ReadingQueueItem, QListWidgetItem] = {}

        self._setup_ui()
        self._setup_drag_drop()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout: quick card, queue card, dual bars, log."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_quick_card())
        layout.addWidget(self._create_queue_card(), 1)

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each source before creating cards.")
        )
        layout.addWidget(self.review_words_checkbox)

        # Overall Progress (item N of M across the run).
        layout.addWidget(self._progress_header(self.tr("Overall Progress")))
        self.overall_progress_widget = ProgressWidget()
        layout.addWidget(self.overall_progress_widget)

        # Current Item Progress (the active item's mining-stage sweep).
        layout.addWidget(self._progress_header(self.tr("Current Item")))
        self.current_progress_widget = ProgressWidget()
        layout.addWidget(self.current_progress_widget)

        # LogWidget (carries its own header + Copy/Clear actions).
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    def _progress_header(self, text: str) -> QLabel:
        """Build a bold section-heading label for a progress bar."""
        header = QLabel(text)
        header.setObjectName("heading3")
        font = QFont()
        font.setPixelSize(FONT_SIZES.body)
        font.setWeight(QFont.Weight.Bold)
        header.setFont(font)
        return header

    def _create_quick_card(self) -> QFrame:
        """Quick Processing card: folder selector + Preview / Mine / Cancel."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        card_layout.setSpacing(SPACING.sm)
        card_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        card_layout.addWidget(SectionHeader(title=self.tr("Quick Processing")))

        self.volume_folder_selector = FileSelector(
            label=self.tr("Volume Folder:"),
            file_mode=False,
            file_filter="",
            label_width=field_label_width("Volume Folder:"),
        )
        self.volume_folder_selector.setToolTip(
            self.tr("A folder with one manga volume mines now; a series folder of many volumes fills the queue below.")
        )
        card_layout.addWidget(self.volume_folder_selector)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.sm)

        self.preview_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_button.setToolTip(self.tr("Preview the selected volume folder — no cards created."))
        self.preview_button.clicked.connect(self._on_preview_clicked)
        button_row.addWidget(self.preview_button)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine the selected volume folder into Anki cards."))
        self.mine_button.clicked.connect(self._on_mine_clicked)
        button_row.addWidget(self.mine_button)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="danger")
        self.cancel_button.setToolTip(self.tr("Cancel the active run."))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()
        button_row.addWidget(self.cancel_button)

        button_row.addStretch()
        card_layout.addLayout(button_row)

        card.setLayout(card_layout)
        card.setMinimumHeight(MIN_HEIGHT_QUEUE_SECTION)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return card

    def _create_queue_card(self) -> QFrame:
        """Manga queue card: Add buttons + list + Process Queue / Clear All."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout()
        card_layout.setSpacing(SPACING.sm)
        card_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        card_layout.addWidget(SectionHeader(title=self.tr("Manga queue")))

        add_row = QHBoxLayout()
        add_row.setSpacing(SPACING.xs)
        self.add_series_button = ModernButton(self.tr("Add Series Folder…"), variant="secondary")
        self.add_series_button.setToolTip(self.tr("Add every volume inside a series folder."))
        self.add_series_button.clicked.connect(self._on_add_series_clicked)
        add_row.addWidget(self.add_series_button)

        self.add_volumes_button = ModernButton(self.tr("Add Volumes…"), variant="secondary")
        self.add_volumes_button.setToolTip(self.tr("Add manga volumes — .mokuro/.cbz/.zip file(s)."))
        self.add_volumes_button.clicked.connect(self._on_add_volumes_clicked)
        add_row.addWidget(self.add_volumes_button)
        add_row.addStretch()
        card_layout.addLayout(add_row)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("reading-queue-list")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setUniformItemSizes(False)
        card_layout.addWidget(self.list_widget, 1)

        self.empty_label = QLabel(self.tr("Add a series folder or volumes above, or drag them here."))
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.empty_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)
        self.process_queue_button = ModernButton(self.tr("Process Queue"), variant="primary")
        self.process_queue_button.setToolTip(self.tr("Mine every queued volume into Anki cards."))
        self.process_queue_button.clicked.connect(self._on_process_queue_clicked)
        button_row.addWidget(self.process_queue_button)

        self.clear_button = ModernButton(self.tr("Clear All"), variant="ghost")
        self.clear_button.setToolTip(self.tr("Remove every queued item that is not currently mining."))
        self.clear_button.clicked.connect(self._on_clear_clicked)
        button_row.addWidget(self.clear_button)
        button_row.addStretch()
        card_layout.addLayout(button_row)

        card.setLayout(card_layout)
        return card

    # ------------------------------------------------------------------
    # Add flow
    # ------------------------------------------------------------------

    def _on_add_series_clicked(self) -> None:
        """Pick a series folder and queue every volume detect finds inside it."""
        if not self.add_series_button.isEnabled():
            return  # Defensive: out-of-band trigger while a run is active.
        folder = QFileDialog.getExistingDirectory(
            self,
            self.tr("Add Series Folder"),
            str(Path.home()),
        )
        if folder:
            self._add_source_path(Path(folder))

    def _on_add_volumes_clicked(self) -> None:
        """Pick manga volume file(s) and queue each (multi-select)."""
        if not self.add_volumes_button.isEnabled():
            return  # Defensive: out-of-band trigger while a run is active.
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("Add Volumes"),
            str(Path.home()),
            f"{self.tr('Manga')} ({_MANGA_FILTER_GLOB})",
        )
        for path in paths:
            self._add_source_path(Path(path))

    def _add_source_path(self, path: Path) -> None:
        """Classify *path* via ``detector.detect`` and queue every resulting ref.

        A series dir yields N volume refs → N rows. Any ``SetupError`` (missing
        sidecar, bad ``.mokuro`` JSON, unrecognized path) or unexpected detect
        failure is surfaced in the log and adds no row.
        """
        try:
            refs = detector.detect(path)
        except SetupError as exc:
            # Crafted, user-facing message: surface it verbatim.
            self.log_widget.append_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - surface any classify failure to the log
            logger.exception("Reading source detect failed for %s", path)
            self.log_widget.append_error(tr_format(self.tr("Could not add %1: %2"), path.name, exc))
            return

        for ref in refs:
            item = self._queue.add(ref)
            self._render_new_item(item)
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Drag-and-drop (tab-level: manga sources queue; novels earn a hint)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag holding a directory or any reading file.

        Novels are accepted too so the drop can be delivered and answered with
        the cross-tab hint (they never create a row here).
        """
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if local.is_dir() or suffix in _MANGA_EXTS or suffix in _NOVEL_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Queue dropped folders / manga files; redirect dropped novels."""
        if event is None:
            return
        novel_seen = False
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            suffix = local.suffix.lower()
            if local.is_dir() or suffix in _MANGA_EXTS:
                self._add_source_path(local)
            elif suffix in _NOVEL_EXTS:
                novel_seen = True
        if novel_seen:
            self.log_widget.append_info(self.tr("Novels are mined in the Novels tab."))
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Quick-card Preview — validate the folder, then preview."""
        self._start_quick_run(preview_mode=True)

    def _on_mine_clicked(self) -> None:
        """Quick-card Mine — validate the folder, then mine."""
        self._start_quick_run(preview_mode=False)

    def _start_quick_run(self, *, preview_mode: bool) -> None:
        """Classify the quick-card folder; mine one volume or fill the queue.

        A single-volume folder mines straight away as an ephemeral item that is
        NOT added to ``self._queue`` (summaries compute over ``_run_items``). A
        series folder of >1 volume expands into queue rows and waits for
        *Process Queue* — the quick card never bulk-mines a series silently.
        """
        if self.worker_thread is not None:
            return
        raw = self.volume_folder_selector.get_path().strip()
        if not raw or not self.volume_folder_selector.is_valid():
            self.log_widget.append_warning(self.tr("Select a valid volume folder first."))
            return

        path = Path(raw)
        try:
            refs = detector.detect(path)
        except SetupError as exc:
            self.log_widget.append_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - surface any classify failure to the log
            logger.exception("Reading source detect failed for %s", path)
            self.log_widget.append_error(tr_format(self.tr("Could not process %1: %2"), path.name, exc))
            return

        if len(refs) > 1:
            # A series folder: don't bulk-mine silently. Add rows + point the
            # user at Process Queue.
            for ref in refs:
                item = self._queue.add(ref)
                self._render_new_item(item)
            self.log_widget.append_info(tr_format(self.tr("Found %1 volumes — added to the queue below."), len(refs)))
            self._recompute_buttons()
            return

        # Exactly one volume: ephemeral item, never entering self._queue.
        ref = refs[0]
        ephemeral = ReadingQueueItem(source=ref, title=ref.title, kind=ref.kind)
        if self._launch_run([ephemeral], preview_mode=preview_mode):
            self._begin_progress(1)
            self._recompute_buttons()

    def _on_process_queue_clicked(self) -> None:
        """Mine every READY queue item (mine-only — preview is the quick card's job)."""
        if self.worker_thread is not None:
            return
        ready = [i for i in self._queue.all_items() if i.status == ReadingItemStatus.READY]
        if not ready:
            return
        if self._launch_run(ready, preview_mode=False):
            self._begin_progress(len(ready))
            self._recompute_buttons()

    def _begin_progress(self, total: int) -> None:
        """Reset both bars and seed the overall bar for a fresh run of *total* items."""
        self.overall_progress_widget.reset()
        self.current_progress_widget.reset()
        self.overall_progress_widget.set_progress(0, total, self.tr("Starting…"))

    def _on_cancel_clicked(self) -> None:
        """Cancel the active run (shared by the quick card and the queue)."""
        # Release any open curation dialog first so the blocked worker resumes
        # instead of hanging on _curation_event (Issue #65).
        self._cancel_active_curation_dialog()
        worker = self.worker_thread
        if worker is None:
            return
        worker.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Refresh the started item's row + seed the current-item bar.

        READ-ONLY: the worker has already set ``status`` to PROCESSING before
        emitting this signal, so the row/bar just reflect current state — never
        write it here (a late-delivered start must not clobber a status the
        worker has since advanced to COMPLETED/ERROR).
        """
        item = self._item_at(idx)
        if item is None:
            return
        self._refresh_row(item)

        total = len(self._run_items)
        self.current_progress_widget.set_status(tr_format(self.tr("Mining %1 of %2: %3"), idx + 1, total, item.title))
        self.current_progress_widget.set_determinate(100)
        self.current_progress_widget.set_value(0)
        self._recompute_buttons()

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Route worker progress into the current-item bar (pct < 0 → indeterminate)."""
        if pct < 0:
            self.current_progress_widget.set_indeterminate()
        else:
            self.current_progress_widget.set_determinate(100)
            self.current_progress_widget.set_value(pct)
        self.current_progress_widget.set_status(label)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome, refresh the row, advance the overall bar.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal, so this slot
        only reads them (via :meth:`_refresh_row` and the overall-bar tally) and
        never writes them.
        """
        item = self._item_at(idx)
        if item is None:
            return

        if error is None:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self.log_widget.append_success(tr_format(self.tr("Mined %1: %2 cards."), item.title, cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the queue worker has
                # already recorded the result; a broken presenter slot shouldn't
                # take down the queue.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        else:
            self.log_widget.append_error(tr_format(self.tr("Failed %1: %2."), item.title, error))

        self._refresh_row(item)

        # Advance the overall bar over items that have reached a terminal state
        # (worker-owned), tallied against the frozen run snapshot.
        total = len(self._run_items)
        done = sum(1 for i in self._run_items if i.status in (ReadingItemStatus.COMPLETED, ReadingItemStatus.ERROR))
        self.overall_progress_widget.set_progress(done, total, tr_format(self.tr("Completed: %1/%2"), done, total))
        self._recompute_buttons()

    def _on_queue_finished(self) -> None:
        """Success-path summary log over the run snapshot. Cleanup is elsewhere.

        ``queue_finished`` is emitted from inside ``run()`` while ``_run_items``
        is still intact; ``QThread.finished`` fires later on every exit path and
        clears it. Computing over ``_run_items`` (not ``self._queue``) covers
        the ephemeral quick-run item, which never enters the queue.
        """
        succeeded = sum(1 for i in self._run_items if i.status == ReadingItemStatus.COMPLETED)
        failed = sum(1 for i in self._run_items if i.status == ReadingItemStatus.ERROR)
        self.log_widget.append_info(tr_format(self.tr("Queue done: %1 succeeded, %2 failed."), succeeded, failed))

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends (called from the base cleanup slot).

        Restores the Cancel button, resets both progress bars, and recomputes
        button state. Runs on every run-exit path (success, cancel, exception).
        """
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self.overall_progress_widget.reset()
        self.current_progress_widget.reset()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Remove + clear
    # ------------------------------------------------------------------

    def _on_remove_clicked(self, item: ReadingQueueItem) -> None:
        """Remove a single item from the queue (and its row from the list)."""
        if item.status == ReadingItemStatus.PROCESSING:
            # The row widget disables its [×] button in this state, but
            # belt-and-braces guard against an out-of-band trigger.
            return
        self._drop_item(item)
        self._recompute_buttons()

    def _on_clear_clicked(self) -> None:
        """Remove every non-PROCESSING item from the queue."""
        # Collect targets first so we don't mutate during iteration.
        targets = [i for i in self._queue.all_items() if i.status != ReadingItemStatus.PROCESSING]
        for item in targets:
            self._drop_item(item)
        # Reset the progress bars only when idle. Mid-run clears must not wipe
        # the live "Mining N of M…" display for the still-PROCESSING item.
        if self.worker_thread is None:
            self.overall_progress_widget.reset()
            self.current_progress_widget.reset()
        self._recompute_buttons()

    def _drop_item(self, item: ReadingQueueItem) -> None:
        """Remove ``item`` from queue model, list widget, and bookkeeping."""
        self._queue.remove(item)
        list_item = self._list_items.pop(item, None)
        if list_item is not None:
            row = self.list_widget.row(list_item)
            if row >= 0:
                # takeItem deletes the QListWidgetItem; Qt manages the embedded
                # widget (deleted alongside the list item).
                self.list_widget.takeItem(row)
        self._row_widgets.pop(item, None)
        # Mid-run removal must also reach the worker: it iterates its own
        # constructor snapshot, so editing the GUI queue alone would still mine
        # the removed item (cards for rows that no longer exist).
        if self.worker_thread is not None:
            self.worker_thread.skip_item(item)

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh every button's enabled/visible state from the queue + worker.

        Pure derived state (no processing flag):

        * Run active → quick Preview/Mine hidden, Cancel shown; Add buttons +
          Process Queue disabled; Clear allowed (trims the queue tail mid-run).
        * Idle → quick Preview/Mine shown; Cancel hidden; Add buttons enabled;
          Process Queue enabled iff a READY item exists; Clear iff non-empty.
        """
        items = self._queue.all_items()
        has_items = bool(items)
        has_ready = any(i.status == ReadingItemStatus.READY for i in items)
        run_active = self.worker_thread is not None

        # Queue card.
        self.add_series_button.setEnabled(not run_active)
        self.add_volumes_button.setEnabled(not run_active)
        self.process_queue_button.setEnabled(has_ready and not run_active)
        # Clear still works during a run for non-PROCESSING items — it's how the
        # user trims the queue tail mid-run.
        self.clear_button.setEnabled(has_items)
        self.empty_label.setVisible(not has_items)

        # Quick card: Preview/Mine give way to Cancel while a run is active.
        self.preview_button.setVisible(not run_active)
        self.mine_button.setVisible(not run_active)
        self.preview_button.setEnabled(not run_active)
        self.mine_button.setEnabled(not run_active)
        self.cancel_button.setVisible(run_active)

    # ------------------------------------------------------------------
    # Row widget integration
    # ------------------------------------------------------------------

    def _render_new_item(self, item: ReadingQueueItem) -> None:
        """Create a row widget for ``item`` and add it to the list widget."""
        widget = ReadingQueueItemWidget(item)
        widget.removed.connect(lambda it=item: self._on_remove_clicked(it))

        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(list_item)
        self.list_widget.setItemWidget(list_item, widget)

        self._row_widgets[item] = widget
        self._list_items[item] = list_item

    def _refresh_row(self, item: ReadingQueueItem) -> None:
        """Update the row widget for ``item`` after the model has changed.

        Tolerant of the ephemeral quick-run item, which has no row: a missing
        entry is a no-op.
        """
        widget = self._row_widgets.get(item)
        if widget is not None:
            widget.update_from(item)

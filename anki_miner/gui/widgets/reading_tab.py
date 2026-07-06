"""Reading (manga / novel) mining tab for the GUI.

Drives a multi-source queue: the user adds manga volumes (a ``.mokuro``
sidecar, a ``.cbz``/``.zip`` archive, or a whole title folder of volumes) and
novels (``.epub``/``.txt``) via the two Add buttons or drag-and-drop; every
accepted path is classified by ``detector.detect`` into one or more queue
rows, and once at least one item is READY the user can run *Preview* or *Mine*
across the whole queue. Structurally a faithful clone of
:class:`~anki_miner.gui.widgets.audiobook_tab.AudiobookTab`, minus the
audio+subtitle file-pair add flow — a reading source is a single path that
``detect`` expands (a title dir yields N volume rows). Two collaborators:

* :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
  — single long-running worker that sweeps the queue sequentially.
* :class:`~anki_miner.gui.widgets.reading_queue_item_widget.ReadingQueueItemWidget`
  — per-row renderer embedded inside a :class:`QListWidget`.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so this tab's signal slots are READ-ONLY on item state: they refresh
the row display and summary counts, never write status/cards/error. This
diverges from the audiobook tab (whose slots write those fields) on purpose —
a queued ``item_started`` slot arriving late must not overwrite a COMPLETED
status back to PROCESSING.

Button enable/disable is recomputed on every queue/worker signal by
:meth:`_recompute_buttons`. There is no explicit state enum — the queue
contents plus the worker handle fully determine the UI.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.reading_queue_item_widget import ReadingQueueItemWidget
from anki_miner.gui.workers.reading_queue_worker import ReadingQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.reading_queue import ReadingItemStatus, ReadingQueue, ReadingQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.reading import detector
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Upper bound for joining the queue worker at shutdown. Generous: covers an
# archive/epub load finishing plus AnkiConnect timeouts. Converts a worst-case
# hang into a bounded delay with a leaked-thread warning.
_SHUTDOWN_WAIT_MS = 30_000

# File-dialog filters for the two Add buttons. Manga is multi-select so a user
# can pick every volume in a title folder at once (each is expanded by detect);
# a whole folder is added by drag-drop or the folder fallback below.
_MANGA_FILTER = "Manga (*.mokuro *.cbz *.zip)"
_BOOK_FILTER = "Books (*.epub *.txt)"

# Extensions accepted from a drop / picked file (dirs are always accepted).
_MANGA_EXTS = (".mokuro", ".cbz", ".zip")
_BOOK_EXTS = (".epub", ".txt")
_READING_EXTS = _MANGA_EXTS + _BOOK_EXTS


class ReadingTab(MiningTabBase):
    """Multi-source manga/novel queue mining tab.

    The tab owns a :class:`ReadingQueue` and at most one running
    :class:`ReadingQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons`.

    The worker→GUI curation bridge is provided by :class:`MiningTabBase`.
    Reading curation is table-only (D8): the worker publishes no media
    context, so the tab inherits the base ``(None, None)`` context — it does
    NOT override :meth:`_build_curation_context`.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None`` so the tab can be constructed before the
                dictionary chain has loaded; the first ``_start_run`` call
                builds one lazily.
            presenter: Optional presenter for routing log messages.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy
                processor rebuilds so reading mining sessions land in
                analytics regardless of whether the processor was passed in at
                construction or built on demand.
        """
        super().__init__(parent)
        self._config = config
        # Optional so release_dictionary_resources() can null it out and
        # _start_run rebuilds lazily on the next user click (Issue #30). Also
        # None on startup-deferred init: app.py skips the eager
        # create_episode_processor call so the window paints faster.
        self._processor: EpisodeProcessor | None = processor
        self._presenter = presenter
        self._stats_service = stats_service

        # Queue model + per-row widget map.
        self._queue: ReadingQueue = ReadingQueue()
        self._row_widgets: dict[ReadingQueueItem, ReadingQueueItemWidget] = {}
        self._list_items: dict[ReadingQueueItem, QListWidgetItem] = {}

        # Active queue worker. Public name preserved for
        # ``MainWindow.closeEvent`` which looks up ``getattr(tab, "worker_thread")``.
        self.worker_thread: ReadingQueueWorker | None = None

        # Set when a config change arrives while a worker is running (OVH-056).
        # _on_worker_finished reconciles: drops the cached processor so the next
        # _start_run rebuilds with the new config.
        self._config_dirty: bool = False

        # Snapshot of the items handed to the active worker, in order. Indexed
        # by the worker's per-item idx signals; frozen at _start_run so mid-run
        # removals of COMPLETED rows don't shift the mapping.
        self._run_items: list[ReadingQueueItem] = []

        # Worker→GUI word-curation bridge (provided by MiningTabBase).
        self._init_curation_bridge()

        self._setup_ui()
        self._setup_drag_drop()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout.

        A QScrollArea wraps a Queue card (Add buttons + list + action
        buttons), a Progress card, and a LogWidget.
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Queue card: Add buttons + list + action buttons
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_layout = QVBoxLayout()
        queue_layout.setSpacing(SPACING.sm)
        queue_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        queue_layout.addWidget(SectionHeader(self.tr("Reading queue")))

        add_row = QHBoxLayout()
        add_row.setSpacing(SPACING.xs)
        self.add_manga_button = ModernButton(self.tr("Add Manga…"), variant="secondary")
        self.add_manga_button.setToolTip(
            self.tr("Add manga volumes — a .mokuro/.cbz/.zip file, or a whole title folder.")
        )
        self.add_manga_button.clicked.connect(self._on_add_manga_clicked)
        add_row.addWidget(self.add_manga_button)

        self.add_book_button = ModernButton(self.tr("Add Book…"), variant="secondary")
        self.add_book_button.setToolTip(self.tr("Add a novel — an .epub or .txt file."))
        self.add_book_button.clicked.connect(self._on_add_book_clicked)
        add_row.addWidget(self.add_book_button)
        add_row.addStretch()
        queue_layout.addLayout(add_row)

        # Queue list
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("reading-queue-list")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setUniformItemSizes(False)
        queue_layout.addWidget(self.list_widget, 1)

        # Empty-state hint (shown when the list is empty).
        self.empty_label = QLabel(self.tr("Add manga or books above, or drag them here."))
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue_layout.addWidget(self.empty_label)

        # Issue #65: opt-in per-item word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each source before creating cards.")
        )
        queue_layout.addWidget(self.review_words_checkbox)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)

        self.preview_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_button.setToolTip(self.tr("Run the queue in preview mode — no cards created."))
        self.preview_button.clicked.connect(self._on_preview_clicked)

        self.mine_button = ModernButton(self.tr("Mine"), variant="primary")
        self.mine_button.setToolTip(self.tr("Mine every queued item into Anki cards."))
        self.mine_button.clicked.connect(self._on_mine_clicked)

        self.clear_button = ModernButton(self.tr("Clear"), variant="ghost")
        self.clear_button.setToolTip(self.tr("Remove every queued item that is not currently mining."))
        self.clear_button.clicked.connect(self._on_clear_clicked)

        self.stop_button = ModernButton(self.tr("Stop All"), variant="danger")
        self.stop_button.setToolTip(self.tr("Cancel the active run."))
        self.stop_button.clicked.connect(self._on_stop_all_clicked)

        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.mine_button)
        button_row.addWidget(self.clear_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch()
        queue_layout.addLayout(button_row)

        queue_card.setLayout(queue_layout)
        layout.addWidget(queue_card)

        # --- Progress card
        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(SPACING.sm)
        progress_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        progress_layout.addWidget(SectionHeader(self.tr("Progress")))
        self.progress_widget = ProgressWidget()
        progress_layout.addWidget(self.progress_widget)

        progress_card.setLayout(progress_layout)
        layout.addWidget(progress_card)

        # --- LogWidget (carries its own header + Copy/Clear actions)
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    # ------------------------------------------------------------------
    # Add flow
    # ------------------------------------------------------------------

    def _on_add_manga_clicked(self) -> None:
        """Pick manga file(s) and queue each (multi-select over a title folder)."""
        if not self.add_manga_button.isEnabled():
            return  # Defensive: out-of-band trigger while a run is active.
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("Add Manga"),
            str(Path.home()),
            _MANGA_FILTER,
        )
        for path in paths:
            self._add_source_path(Path(path))

    def _on_add_book_clicked(self) -> None:
        """Pick novel file(s) and queue each."""
        if not self.add_book_button.isEnabled():
            return  # Defensive: out-of-band trigger while a run is active.
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("Add Book"),
            str(Path.home()),
            _BOOK_FILTER,
        )
        for path in paths:
            self._add_source_path(Path(path))

    def _add_source_path(self, path: Path) -> None:
        """Classify *path* via ``detector.detect`` and queue every resulting ref.

        A title dir yields N volume refs → N rows. Any ``SetupError`` (missing
        sidecar, bad ``.mokuro`` JSON, unrecognized path) or unexpected detect
        failure is surfaced in the log and adds no row — mirroring the
        audiobook tab's per-add error precedent.
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
    # Drag-and-drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag if any URL is a directory or a recognized reading file."""
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            if local.is_dir() or local.suffix.lower() in _READING_EXTS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Route each dropped directory / reading file through ``detect``."""
        if event is None:
            return
        for url in urls_from_event(event):
            local = Path(url.toLocalFile())
            if local.is_dir() or local.suffix.lower() in _READING_EXTS:
                self._add_source_path(local)
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Preview button — runs the queue with ``preview_mode=True``."""
        self._start_run(preview_mode=True)

    def _on_mine_clicked(self) -> None:
        """Mine button — runs the queue with ``preview_mode=False``."""
        self._start_run(preview_mode=False)

    def _start_run(self, *, preview_mode: bool) -> None:
        """Construct and start a :class:`ReadingQueueWorker` over READY items."""
        if self.worker_thread is not None:
            return
        ready_items = [i for i in self._queue.all_items() if i.status == ReadingItemStatus.READY]
        if not ready_items:
            return

        # Processor may be None for two reasons: (a) Settings → Remove dictionary
        # called release_dictionary_resources to drop sqlite handles, or (b)
        # app.py deferred the eager create_episode_processor call so the window
        # could paint faster on startup. Either way it is rebuilt lazily so the
        # user doesn't have to restart. When it must be rebuilt we hand a factory
        # to the worker so the slow registry/sqlite/CSV construction runs off the
        # GUI thread; _on_worker_finished caches the built processor back into
        # self._processor so subsequent runs reuse it (and Remove-dictionary can
        # release it). When it is already cached we pass it directly (cheap).
        processor_factory = None
        if self._processor is None:
            presenter = self._presenter
            if presenter is None:
                self.log_widget.append_warning(self.tr("Mining unavailable — services not initialized."))
                return

            def processor_factory() -> EpisodeProcessor:
                return create_episode_processor(
                    self._config,
                    presenter,
                    stats_service=self._stats_service,  # type: ignore[arg-type]
                )

        # Snapshot BEFORE constructing the worker so all idx-based signal
        # handlers resolve against a frozen list that survives mid-run removals.
        self._run_items = list(ready_items)

        self.progress_widget.reset()

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        worker = ReadingQueueWorker(
            processor=self._processor,
            config=self._config,
            items=ready_items,
            curation_callback=curation_cb,
            preview_mode=preview_mode,
            processor_factory=processor_factory,
        )
        worker.item_started.connect(self._on_item_started)
        worker.item_progress.connect(self._on_item_progress)
        worker.item_finished.connect(self._on_item_finished)
        worker.queue_finished.connect(self._on_queue_finished)
        # Fatal pre-loop failures (schema-stale dict gate, processor build) end
        # the run via error + queue_finished; surface the message in the log.
        worker.error.connect(self.log_widget.append_error)
        # QThread.finished fires on every run() exit (success, cancel, exception),
        # so run-end cleanup converges here rather than only on the success path.
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker

        mode_label = self.tr("Preview") if preview_mode else self.tr("Mine")
        self.log_widget.append_info(tr_format(self.tr("%1 run starting — %2 items."), mode_label, len(ready_items)))
        self._recompute_buttons()
        worker.start()

    def _on_stop_all_clicked(self) -> None:
        """Cancel the active run."""
        # Release any open curation dialog first so the blocked worker resumes
        # instead of hanging on _curation_event (Issue #65).
        self._cancel_active_curation_dialog()
        worker = self.worker_thread
        if worker is None:
            return
        worker.cancel()
        self.stop_button.setEnabled(False)
        self.stop_button.setText(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Per-item signal slots (READ-ONLY on item state — the worker owns it)
    # ------------------------------------------------------------------

    def _item_at(self, idx: int) -> ReadingQueueItem | None:
        """Map a worker-emitted ``idx`` back to a queue item.

        Resolves against ``_run_items`` — the snapshot taken at :meth:`_start_run`.
        Because the snapshot is frozen, mid-run removals of COMPLETED rows do not
        shift the mapping.
        """
        if 0 <= idx < len(self._run_items):
            return self._run_items[idx]
        return None

    def _on_item_started(self, idx: int) -> None:
        """Refresh the started item's row + progress text.

        READ-ONLY: the worker has already set ``status`` to PROCESSING before
        emitting this signal, so the row/progress just reflect current state —
        never write it here (a late-delivered start must not clobber a status
        the worker has since advanced to COMPLETED/ERROR).
        """
        item = self._item_at(idx)
        if item is None:
            return
        self._refresh_row(item)

        total = len(self._run_items)
        self.progress_widget.set_status(tr_format(self.tr("Mining %1 of %2: %3"), idx + 1, total, item.title))
        self.progress_widget.set_determinate(100)
        self.progress_widget.set_value(0)
        self._recompute_buttons()

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Route worker progress into the progress widget."""
        # Translation mirrors the queue worker's progress adapter:
        # pct < 0 → indeterminate; otherwise determinate.
        if pct < 0:
            self.progress_widget.set_indeterminate()
        else:
            self.progress_widget.set_determinate(100)
            self.progress_widget.set_value(pct)
        self.progress_widget.set_status(label)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Log the outcome, refresh the row, forward the result to the presenter.

        READ-ONLY: the worker has already recorded ``status``/``cards_created``/
        ``error_message`` on the item before emitting this signal, so this slot
        only reads them (via :meth:`_refresh_row`) and never writes them.
        """
        item = self._item_at(idx)
        if item is None:
            return

        if error is None:
            cards = int(getattr(result, "cards_created", 0) or 0)
            self.log_widget.append_success(tr_format(self.tr("Mined %1: %2 cards."), item.title, cards))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the queue worker has
                # already recorded the result; a broken presenter slot
                # shouldn't take down the queue.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        else:
            self.log_widget.append_error(tr_format(self.tr("Failed %1: %2."), item.title, error))

        self._refresh_row(item)
        self._recompute_buttons()

    def _on_queue_finished(self) -> None:
        """Success-path summary log. State cleanup runs in `_on_worker_finished`.

        ``queue_finished`` is emitted from inside ``run()``; ``QThread.finished``
        fires later on every exit path. Splitting the two keeps cleanup on the
        single converged path while still logging a per-run summary.
        """
        succeeded = sum(1 for i in self._queue.all_items() if i.status == ReadingItemStatus.COMPLETED)
        failed = sum(1 for i in self._queue.all_items() if i.status == ReadingItemStatus.ERROR)
        self.log_widget.append_info(tr_format(self.tr("Queue done: %1 succeeded, %2 failed."), succeeded, failed))

    def _on_worker_finished(self) -> None:
        """Single cleanup slot wired to ``QThread.finished``.

        Fires after ``run()`` returns regardless of path (success, mid-mine
        cancel, unhandled exception), so worker state and the progress widget
        always recover instead of stranding a stale label / a leaked handle.

        Reconciles a deferred config change (OVH-056): if ``_config_dirty`` is
        set, close + null the processor so the next _start_run rebuilds with the
        config that arrived mid-run.
        """
        # Cache the processor the worker built (factory path) BEFORE nulling
        # worker_thread, so subsequent runs reuse it and Remove-dictionary can
        # release it. No-op when _processor was already set (prebuilt path).
        if self._processor is None and self.worker_thread is not None:
            self._processor = self.worker_thread.curation_processor
        self.worker_thread = None
        self._run_items = []
        self.stop_button.setText(self.tr("Stop All"))
        self.stop_button.setEnabled(True)
        self.progress_widget.reset()
        self._recompute_buttons()
        if self._config_dirty:
            if self._processor is not None:
                self._processor.close()
                self._processor = None
            self._config_dirty = False

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
        # Reset the progress widget only when idle. Mid-run clears must not wipe
        # the live "Mining N of M…" display for the still-PROCESSING item.
        if self.worker_thread is None:
            self.progress_widget.reset()
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

        Derived from queue contents + worker handle:

        * Run active → Add/Preview/Mine disabled, Stop visible, Clear allowed.
        * Otherwise → Add enabled; Preview/Mine enabled iff a READY item
          exists; Clear enabled iff the queue is non-empty; Stop hidden.
        """
        items = self._queue.all_items()
        has_items = bool(items)
        has_ready = any(i.status == ReadingItemStatus.READY for i in items)
        run_active = self.worker_thread is not None

        self.add_manga_button.setEnabled(not run_active)
        self.add_book_button.setEnabled(not run_active)
        self.preview_button.setEnabled(has_ready and not run_active)
        self.mine_button.setEnabled(has_ready and not run_active)
        # Clear still works during a run for non-PROCESSING items — it's how the
        # user trims the tail mid-run.
        self.clear_button.setEnabled(has_items)

        if run_active:
            self.stop_button.show()
        else:
            self.stop_button.hide()

        # Empty-state hint vs list visibility.
        self.empty_label.setVisible(not has_items)

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
        """Update the row widget for ``item`` after the model has changed."""
        widget = self._row_widgets.get(item)
        if widget is not None:
            widget.update_from(item)

    # ------------------------------------------------------------------
    # Curation bridge
    # ------------------------------------------------------------------
    #
    # D8: intentionally NO ``_build_curation_context`` override. Reading
    # curation is table-only — the ReadingQueueWorker publishes no
    # ``_curation_video``/``_curation_subtitle``/``_curation_offset``, so the
    # base ``MiningTabBase._build_curation_context`` (returns ``(None, None)``)
    # is exactly right. Cloning the audiobook override would AttributeError on
    # those missing worker attributes.

    def _mark_known(self, forms: set[str]) -> int:
        """Persist curator-selected forms to the local known/ignore list (Issue #42).

        Writes immediately (source='user') so words persist even if the dialog is
        cancelled. Builds the DB ad hoc from the config path.
        """
        from anki_miner.services.known_word_db import KnownWordDB

        db = KnownWordDB(self._config.known_words_db_path)
        db.initialize()
        return db.add_words(forms, source="user")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new frozen config and refresh config-dependent services.

        For the processor — which owns open SQLite handles + a requests.Session —
        uses a lazy-drop strategy instead of an eager rebuild (OVH-014):

        * If idle: close() + null the cached processor so the next _start_run
          rebuilds with the current config (off the incidental-refresh path).
        * If busy: set ``_config_dirty`` instead of touching the running
          processor — closing providers under a live worker crashes the run
          (OVH-056). ``_on_worker_finished`` reconciles after the run ends.

        Args:
            config: New frozen configuration.
        """
        self._config = config

        worker_busy = self.worker_thread is not None and self.worker_thread.isRunning()
        if worker_busy:
            # Mark dirty; reconcile in _on_worker_finished (OVH-056).
            self._config_dirty = True
        else:
            # Lazy drop: close the old processor (dict sqlite + audio Session —
            # OVH-055; Issue #30) and null it out. _start_run rebuilds when
            # None, threading stats_service through.
            if self._processor is not None:
                self._processor.close()
                self._processor = None

    def release_dictionary_resources(self) -> bool:
        """Close any cached dictionary handles so the file can be deleted.

        Used by Settings → Dictionary Settings → Remove to drop SQLite handles
        before ``rmtree`` (Issue #30, Win11 file-lock). Returns ``False`` while a
        mining run is in flight — closing providers under an active worker would
        crash the run. Returns ``True`` after a successful release, or when there
        was nothing to release.

        The processor is rebuilt lazily on the next Preview/Mine click via
        ``_start_run``.
        """
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return False
        if self._processor is not None:
            self._processor.release_dictionary_resources()
            self._processor = None
        return True

    def shutdown(self) -> None:
        """Stop the active worker.

        Called by :class:`MainWindow` during closeEvent so that background
        threads don't outlive the application.
        """
        if self.worker_thread is not None:
            # Release any open curation dialog first so a worker blocked in
            # _curation_event.wait() resumes (Issue #65). cancel() alone only
            # sets _cancel_event, not _curation_event.
            self._cancel_active_curation_dialog()
            self.worker_thread.cancel()
            # The dialog release above only helps once the dialog exists. If the
            # worker emitted _curation_requested but the queued slot has not run
            # yet, blocking in wait() below would deadlock: this GUI thread is
            # the only one that could run the slot. Poison the gate so a parked
            # (or about-to-park) worker falls through.
            self._poison_curation_gate()
            self.worker_thread.quit()
            if not self.worker_thread.wait(_SHUTDOWN_WAIT_MS):
                logger.warning(
                    "Reading queue worker did not stop within %sms at shutdown; leaking thread",
                    _SHUTDOWN_WAIT_MS,
                )
            self.worker_thread = None

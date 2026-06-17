"""YouTube mining tab for the GUI.

Drives a multi-URL queue: the user pastes URLs, each one is probed
asynchronously, and once at least one item is READY the user can run
*Preview* or *Mine* across the whole queue. The tab itself is a thin shell
around three collaborators:

* :class:`~anki_miner.gui.widgets.youtube_playlist_flow.PlaylistAddController`
  — owns the Add flow: input gating (T-34), the parallel single-video probe
  workers, and the playlist resolve/confirm/expand detour (Issue #70).
* :class:`~anki_miner.gui.workers.youtube_queue_worker.YouTubeQueueWorker` —
  single long-running worker that sweeps the queue sequentially.
* :class:`~anki_miner.gui.widgets.youtube_queue_item_widget.YouTubeQueueItemWidget` —
  per-row renderer embedded inside a :class:`QListWidget`.

Button enable/disable is recomputed on every queue/worker signal by
:meth:`_recompute_buttons`. There is no explicit state enum — the
queue contents plus the worker handle (and the add-flow controller's
``is_active``) fully determine the UI.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.service_factory import create_episode_processor, create_youtube_fetcher
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.youtube_playlist_flow import PlaylistAddCallbacks, PlaylistAddController
from anki_miner.gui.widgets.youtube_queue_item_widget import YouTubeQueueItemWidget
from anki_miner.gui.workers.youtube_queue_worker import YouTubeQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueue, YouTubeQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

logger = logging.getLogger(__name__)

# Upper bound for joining the queue worker at shutdown. Generous: covers the
# fetcher's cancel watchdog poll plus the psutil kill grace. Converts a
# worst-case hang into a bounded delay with a leaked-thread warning.
_SHUTDOWN_WAIT_MS = 30_000


class YouTubeTab(MiningTabBase):
    """Multi-URL YouTube queue mining tab.

    The tab owns a :class:`YouTubeQueue`, a :class:`PlaylistAddController`
    holding the in-flight probe/playlist workers, and at most one running
    :class:`YouTubeQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons`.

    The worker→GUI curation bridge is provided by :class:`MiningTabBase`.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None,
        fetcher: YouTubeFetcherService,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None`` so the tab can be constructed before the
                dictionary chain has loaded; the first ``_start_run`` call builds
                one lazily.
            fetcher: YouTube fetcher service used for metadata probes and,
                indirectly via ``processor.process_youtube_url``, downloads.
            presenter: Optional presenter for routing log messages.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy
                processor rebuilds so YouTube mining sessions land in
                analytics regardless of whether the processor was passed
                in at construction or built on demand.
        """
        super().__init__(parent)
        self._config = config
        # Optional so release_dictionary_resources() can null it out and
        # _start_run rebuilds lazily on the next user click (Issue #30).
        # Also None on startup-deferred init: app.py skips the eager
        # create_episode_processor call so the window paints faster.
        self._processor: EpisodeProcessor | None = processor
        self._fetcher = fetcher
        self._presenter = presenter
        self._stats_service = stats_service

        # Queue model + per-row widget map.
        self._queue: YouTubeQueue = YouTubeQueue()
        self._row_widgets: dict[YouTubeQueueItem, YouTubeQueueItemWidget] = {}
        self._list_items: dict[YouTubeQueueItem, QListWidgetItem] = {}

        # Active queue worker. Public name preserved for
        # ``MainWindow.closeEvent`` which looks up ``getattr(tab, "worker_thread")``.
        self.worker_thread: YouTubeQueueWorker | None = None

        # Snapshot of the items handed to the active worker, in order.
        # Indexed by the worker's per-item idx signals; frozen at _start_run
        # so mid-run removals of COMPLETED rows don't shift the mapping.
        self._run_items: list[YouTubeQueueItem] = []

        # Worker→GUI word-curation bridge (provided by MiningTabBase).
        self._init_curation_bridge()

        self._setup_ui()

        # Add-flow controller: probe workers, playlist resolve/expand, choice
        # dialog (Issue #70). Constructed after _setup_ui so the widget-bound
        # callbacks (url_edit, log_widget) exist; the tab stays the Qt parent
        # of every spawned worker thread.
        self._add_flow = PlaylistAddController(
            fetcher=fetcher,
            config=config,
            callbacks=PlaylistAddCallbacks(
                enqueue=self._queue.add,
                queued_items=self._queue.all_items,
                render_new_item=self._render_new_item,
                refresh_row=self._refresh_row,
                recompute_buttons=self._recompute_buttons,
                clear_url_input=self.url_edit.clear,
                log_info=self.log_widget.append_info,
                log_warning=self.log_widget.append_warning,
                log_error=self.log_widget.append_error,
            ),
            parent=self,
        )

        self._recompute_buttons()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the tab layout.

        A QScrollArea wraps a Queue card (URL input + list + action buttons),
        a Progress card, and a LogWidget.
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # --- Queue card: URL row + list + action buttons
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_layout = QVBoxLayout()
        queue_layout.setSpacing(SPACING.sm)
        queue_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        queue_layout.addWidget(SectionHeader("YouTube queue"))

        # URL row
        url_row = QHBoxLayout()
        url_row.setSpacing(SPACING.xs)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_edit.returnPressed.connect(self._on_add_clicked)
        url_row.addWidget(self.url_edit, 1)

        self.add_button = ModernButton("Add", variant="secondary")
        self.add_button.setToolTip("Add the URL to the queue and probe its metadata.")
        self.add_button.clicked.connect(self._on_add_clicked)
        url_row.addWidget(self.add_button)
        queue_layout.addLayout(url_row)

        # Queue list
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("yt-queue-list")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setUniformItemSizes(False)
        queue_layout.addWidget(self.list_widget, 1)

        # Empty-state hint (shown when the list is empty).
        self.empty_label = QLabel("Paste a YouTube URL above and click Add.")
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue_layout.addWidget(self.empty_label)

        # Issue #65: opt-in per-video word curation popup (default off).
        self.review_words_checkbox = QCheckBox("Review words before mining")
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip("Show the word-selection popup for each video before creating cards.")
        queue_layout.addWidget(self.review_words_checkbox)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.xs)

        self.preview_button = ModernButton("Preview", variant="secondary")
        self.preview_button.setToolTip("Run the queue in preview mode — no cards created.")
        self.preview_button.clicked.connect(self._on_preview_clicked)

        self.mine_button = ModernButton("Mine", variant="primary")
        self.mine_button.setToolTip("Mine every READY item in the queue into Anki cards.")
        self.mine_button.clicked.connect(self._on_mine_clicked)

        self.clear_button = ModernButton("Clear", variant="ghost")
        self.clear_button.setToolTip("Remove every queued item that is not currently mining.")
        self.clear_button.clicked.connect(self._on_clear_clicked)

        self.stop_button = ModernButton("Stop All", variant="danger")
        self.stop_button.setToolTip("Cancel the active run.")
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

        progress_layout.addWidget(SectionHeader("Progress"))
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
    # Add flow (delegated to PlaylistAddController)
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Hand the current URL to the add-flow controller."""
        if not self.add_button.isEnabled():
            return  # Defensive: returnPressed fires even when the button is disabled.
        url = self.url_edit.text().strip()
        if not url:
            return
        self._add_flow.begin(url)

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
        """Construct and start a :class:`YouTubeQueueWorker` over READY items."""
        if self.worker_thread is not None:
            return
        ready_items = [i for i in self._queue.all_items() if i.status == YouTubeItemStatus.READY]
        if not ready_items:
            return

        # Processor may be None for two reasons: (a) Settings → Remove dictionary
        # called release_dictionary_resources to drop sqlite handles, or (b)
        # app.py deferred the eager create_episode_processor call so the window
        # could paint faster on startup. Either way, rebuild lazily so the user
        # doesn't have to restart the app — and pass stats_service through so
        # mining sessions still land in analytics.
        if self._processor is None and self._presenter is not None:
            self._processor = create_episode_processor(
                self._config,
                self._presenter,
                stats_service=self._stats_service,  # type: ignore[arg-type]
            )
        if self._processor is None:
            self.log_widget.append_warning("Mining unavailable — services not initialized.")
            return

        # Snapshot BEFORE constructing the worker so all idx-based signal
        # handlers resolve against a frozen list that survives mid-run removals.
        self._run_items = list(ready_items)

        self.progress_widget.reset()

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        worker = YouTubeQueueWorker(
            processor=self._processor,
            config=self._config,
            items=ready_items,
            curation_callback=curation_cb,
            preview_mode=preview_mode,
        )
        worker.item_started.connect(self._on_item_started)
        worker.item_progress.connect(self._on_item_progress)
        worker.item_finished.connect(self._on_item_finished)
        worker.queue_finished.connect(self._on_queue_finished)
        # QThread.finished fires on every run() exit (success, cancel, exception),
        # so run-end cleanup converges here rather than only on the success path.
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker

        mode_label = "Preview" if preview_mode else "Mine"
        self.log_widget.append_info(f"{mode_label} run starting — {len(ready_items)} items.")
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
        self.stop_button.setText("Cancelling…")

    # ------------------------------------------------------------------
    # Per-item signal slots
    # ------------------------------------------------------------------

    def _item_at(self, idx: int) -> YouTubeQueueItem | None:
        """Map a worker-emitted ``idx`` back to a queue item.

        Resolves against ``_run_items`` — the snapshot taken at :meth:`_start_run`.
        Because the snapshot is frozen, mid-run removals of COMPLETED rows do not
        shift the mapping.
        """
        if 0 <= idx < len(self._run_items):
            return self._run_items[idx]
        return None

    def _on_item_started(self, idx: int) -> None:
        """Mark the item as PROCESSING and update progress text."""
        item = self._item_at(idx)
        if item is None:
            return
        item.status = YouTubeItemStatus.PROCESSING
        self._refresh_row(item)

        total = len(self._run_items)
        title = item.video_info.title if item.video_info else item.url
        self.progress_widget.set_status(f"Mining {idx + 1} of {total}: {title}")
        self.progress_widget.set_determinate(100)
        self.progress_widget.set_value(0)
        self._recompute_buttons()

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Route worker progress into the progress widget."""
        # Translation mirrors YouTubeQueueWorker's progress adapter:
        # pct < 0 → indeterminate; otherwise determinate.
        if pct < 0:
            self.progress_widget.set_indeterminate()
        else:
            self.progress_widget.set_determinate(100)
            self.progress_widget.set_value(pct)
        self.progress_widget.set_status(label)

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Update the item with success/error and forward to the presenter."""
        item = self._item_at(idx)
        if item is None:
            return

        if error is None:
            cards = int(getattr(result, "cards_created", 0) or 0)
            item.status = YouTubeItemStatus.COMPLETED
            item.cards_created = cards
            item.error_message = None
            self.log_widget.append_success(f"Mined {item.url}: {cards} cards (attempts={attempts}).")
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the queue worker has
                # already recorded the result; a broken presenter slot
                # shouldn't take down the queue.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        else:
            item.status = YouTubeItemStatus.ERROR
            item.error_message = str(error)
            self.log_widget.append_error(f"Failed {item.url}: {error} (attempts={attempts}).")

        self._refresh_row(item)
        self._recompute_buttons()

    def _on_queue_finished(self) -> None:
        """Success-path summary log. State cleanup runs in `_on_worker_finished`.

        ``queue_finished`` is emitted from inside ``run()`` and only on the
        non-cancelled path; ``QThread.finished`` fires later on every exit
        path. Splitting the two keeps the cancel-mid-fetch case from leaking
        worker state, while still logging a per-run summary on success.
        """
        succeeded = sum(1 for i in self._queue.all_items() if i.status == YouTubeItemStatus.COMPLETED)
        failed = sum(1 for i in self._queue.all_items() if i.status == YouTubeItemStatus.ERROR)
        self.log_widget.append_info(f"Queue done: {succeeded} succeeded, {failed} failed.")

    def _on_worker_finished(self) -> None:
        """Single cleanup slot wired to ``QThread.finished``.

        Fires after ``run()`` returns regardless of path (success, mid-fetch
        cancel, unhandled exception), so worker state and the progress widget
        always recover instead of stranding ``"Merging"`` / a leaked handle.
        """
        self.worker_thread = None
        self._run_items = []
        self.stop_button.setText("Stop All")
        self.stop_button.setEnabled(True)
        self.progress_widget.reset()
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Remove + clear
    # ------------------------------------------------------------------

    def _on_remove_clicked(self, item: YouTubeQueueItem) -> None:
        """Remove a single item from the queue (and its row from the list)."""
        if item.status == YouTubeItemStatus.PROCESSING:
            # The row widget disables its [×] button in this state, but
            # belt-and-braces guard against an out-of-band trigger.
            return
        self._drop_item(item)
        self._recompute_buttons()

    def _on_clear_clicked(self) -> None:
        """Remove every non-PROCESSING item from the queue."""
        # Invalidate pending playlist work (late-resolve generation bump +
        # entry-probe cancel) — that state lives on the add-flow controller.
        self._add_flow.invalidate_pending()
        # Collect targets first so we don't mutate during iteration.
        targets = [i for i in self._queue.all_items() if i.status != YouTubeItemStatus.PROCESSING]
        for item in targets:
            self._drop_item(item)
        # Reset the progress widget only when idle. Mid-run clears must not wipe
        # the live "Mining N of M…" / fetch progress display for the still-PROCESSING item.
        if self.worker_thread is None:
            self.progress_widget.reset()
        self._recompute_buttons()

    def _drop_item(self, item: YouTubeQueueItem) -> None:
        """Remove ``item`` from queue model, list widget, and bookkeeping."""
        self._queue.remove(item)
        list_item = self._list_items.pop(item, None)
        if list_item is not None:
            row = self.list_widget.row(list_item)
            if row >= 0:
                # takeItem deletes the QListWidgetItem; Qt manages the
                # embedded widget (deleted alongside the list item).
                self.list_widget.takeItem(row)
        self._row_widgets.pop(item, None)
        # Mid-run removal must also reach the worker: it iterates its own
        # constructor snapshot, so editing the GUI queue alone would still
        # fetch + mine the removed item (cards for rows that no longer exist).
        if self.worker_thread is not None:
            self.worker_thread.skip_item(item)

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh every button's enabled/visible state from the queue + worker.

        Derived from queue contents + worker handle:

        * Run active → Add/Preview/Mine disabled, Stop visible, Clear allowed.
        * Playlist resolve pending → Add disabled (everything else unchanged).
        * Otherwise → Add enabled; Preview/Mine/Clear enabled iff a READY
          item exists; Stop hidden.
        """
        items = self._queue.all_items()
        has_items = bool(items)
        has_ready = any(i.status == YouTubeItemStatus.READY for i in items)
        run_active = self.worker_thread is not None
        resolve_active = self._add_flow.is_active

        # Add also locks while a playlist resolve is pending — a second Add
        # mid-resolve would race the confirmation dialog.
        self.add_button.setEnabled(not run_active and not resolve_active)
        self.preview_button.setEnabled(has_ready and not run_active)
        self.mine_button.setEnabled(has_ready and not run_active)
        # Clear still works during a run for non-PROCESSING items — it's how
        # the user trims the tail mid-run.
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

    def _render_new_item(self, item: YouTubeQueueItem) -> None:
        """Create a row widget for ``item`` and add it to the list widget."""
        widget = YouTubeQueueItemWidget(item)
        widget.removed.connect(lambda it=item: self._on_remove_clicked(it))

        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(list_item)
        self.list_widget.setItemWidget(list_item, widget)

        self._row_widgets[item] = widget
        self._list_items[item] = list_item

    def _refresh_row(self, item: YouTubeQueueItem) -> None:
        """Update the row widget for ``item`` after the model has changed."""
        widget = self._row_widgets.get(item)
        if widget is not None:
            widget.update_from(item)

    # ------------------------------------------------------------------
    # Curation bridge
    # ------------------------------------------------------------------

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Build (media_context, lookup_fn) from the live worker's fetched media.

        The worker is blocked in ``_curation_event.wait()`` while this runs, so
        reading its ``_curation_*`` attributes is race-free. Mirrors
        ``BatchProcessingTab._build_curation_context`` for player + dictionary
        parity.
        """
        w = self.worker_thread
        if w is None:
            return None, None
        media_context = self._make_curation_media_context(
            self._config, w._curation_video, w._curation_subtitle, offset=w._curation_offset
        )
        return media_context, self._lookup_fn_from_processor(w.curation_processor)

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

        Always rebuilds the fetcher (cheap; snapshots config in the ctor).
        Only rebuilds the processor when no run is active — DefinitionService
        caches a provider chain that may have an open SQLite connection.

        Args:
            config: New frozen configuration.
        """
        self._config = config
        self._fetcher = create_youtube_fetcher(config)
        # Push the new snapshot into the add-flow controller so future probes
        # classify against the updated limits; in-flight workers captured the
        # old fetcher at construction and are unaffected.
        self._add_flow.update_config(config, self._fetcher)

        worker_busy = self.worker_thread is not None and self.worker_thread.isRunning()
        if not worker_busy and self._presenter is not None:
            old_processor = self._processor
            # Thread the tab's own stats service through, not
            # ``getattr(self._processor, …)``: a config change before the first
            # run (processor still startup-deferred / None) would otherwise pass
            # None and silently disable stats.db for the session (T-15). Mirrors
            # the lazy rebuild in _start_run.
            self._processor = create_episode_processor(
                config,
                self._presenter,
                stats_service=self._stats_service,  # type: ignore[arg-type]
            )
            # Full close of the discarded processor: dict sqlite handles AND the
            # expression-audio requests.Session (release_dictionary_resources()
            # was dict-only and leaked the Session — OVH-055).  Issue #30
            # Windows file-lock is covered by close() which calls
            # release_dictionary_resources() internally.
            if old_processor is not None:
                old_processor.close()

    def release_dictionary_resources(self) -> bool:
        """Close any cached dictionary handles so the file can be deleted.

        Used by Settings → Dictionary Settings → Remove to drop SQLite handles
        before ``rmtree`` (Issue #30, Win11 file-lock). Returns ``False`` while
        a mining run is in flight — closing providers under an active worker
        would crash the run. Returns ``True`` after a successful release, or
        when there was nothing to release.

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
        """Stop the active worker and tear down probe workers.

        Called by :class:`MainWindow` during closeEvent so that background
        threads don't outlive the application. The probe/playlist workers are
        owned by the add-flow controller, which gets its own shutdown call
        after the queue worker is joined — same teardown order as before the
        extraction.
        """
        if self.worker_thread is not None:
            # Release any open curation dialog first so a worker blocked in
            # _curation_event.wait() resumes (Issue #65). cancel() alone only
            # sets _cancel_event, not _curation_event.
            self._cancel_active_curation_dialog()
            self.worker_thread.cancel()
            # The dialog release above only helps once the dialog exists. If
            # the worker emitted _curation_requested but the queued slot has
            # not run yet, blocking in wait() below would deadlock: this GUI
            # thread is the only one that could run the slot. Poison the gate
            # so a parked (or about-to-park) worker falls through.
            self._poison_curation_gate()
            self.worker_thread.quit()
            if not self.worker_thread.wait(_SHUTDOWN_WAIT_MS):
                logger.warning(
                    "YouTube queue worker did not stop within %sms at shutdown; leaking thread",
                    _SHUTDOWN_WAIT_MS,
                )
            self.worker_thread = None

        self._add_flow.shutdown()

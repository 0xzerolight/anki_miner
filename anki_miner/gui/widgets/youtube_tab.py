"""YouTube mining tab for the GUI.

Drives a multi-URL queue: the user pastes URLs, each one is probed
asynchronously, and once at least one item is READY the user can run
*Preview* or *Mine* across the whole queue. The tab itself is a thin shell
around three collaborators:

* :class:`~anki_miner.gui.workers.youtube_probe_worker.YouTubeProbeWorker` —
  one short-lived QThread per Add click, run in parallel.
* :class:`~anki_miner.gui.workers.youtube_queue_worker.YouTubeQueueWorker` —
  single long-running worker that sweeps the queue sequentially.
* :class:`~anki_miner.gui.widgets.youtube_queue_item_widget.YouTubeQueueItemWidget` —
  per-row renderer embedded inside a :class:`QListWidget`.

Button enable/disable is recomputed on every queue/worker signal by
:meth:`_recompute_buttons`. There is no explicit state enum — the
queue contents plus the worker handle fully determine the UI.
"""

from __future__ import annotations

import contextlib
import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
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
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.widgets.youtube_queue_item_widget import YouTubeQueueItemWidget
from anki_miner.gui.workers.youtube_probe_worker import YouTubeProbeWorker
from anki_miner.gui.workers.youtube_queue_worker import YouTubeQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.youtube import SubMode, VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueue, YouTubeQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.youtube_fetcher import YouTubeFetcherService


def _classify_probe_result(info: VideoInfo, config: AnkiMinerConfig) -> tuple[bool, str | None, SubMode | None]:
    """Classify a probe result.

    Returns:
        (is_mineable, error_message, resolved_sub_mode). On success
        ``is_mineable`` is True, ``error_message`` is None, and
        ``resolved_sub_mode`` is the chosen sub mode. On failure the
        triple's first element is False and ``error_message`` describes
        why the video cannot be mined.
    """
    if info.is_live:
        return False, "Live streams are not supported.", None
    if info.duration_s > config.youtube_max_duration_s:
        minutes_limit = max(1, config.youtube_max_duration_s // 60)
        return False, f"Video exceeds max duration ({minutes_limit} min).", None
    if info.is_age_restricted and not config.youtube_cookies_from_browser:
        return (
            False,
            "Age-restricted video. Set Cookies → Browser in Settings and retry.",
            None,
        )
    if info.has_manual_ja_subs:
        return True, None, "manual_only"
    if info.has_auto_ja_subs:
        return True, None, "auto_only"
    return False, "No Japanese subtitles available for this video.", None


class YouTubeTab(QWidget):
    """Multi-URL YouTube queue mining tab.

    The tab owns a :class:`YouTubeQueue`, a list of in-flight
    :class:`YouTubeProbeWorker` instances, and at most one running
    :class:`YouTubeQueueWorker`. Button state is derived from the queue
    contents and the worker handle via :meth:`_recompute_buttons`.
    """

    # Cross-thread curation bridge: emitted from the worker thread, handled on
    # the GUI thread. Mirrors the pattern in SingleEpisodeTab.
    _curation_requested = pyqtSignal(list)

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor,
        fetcher: YouTubeFetcherService,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the tab.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (shared across tabs).
            fetcher: YouTube fetcher service used for metadata probes and,
                indirectly via ``processor.process_youtube_url``, downloads.
            presenter: Optional presenter for routing log messages.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._config = config
        self._processor = processor
        self._fetcher = fetcher
        self._presenter = presenter

        # Queue model + per-row widget map.
        self._queue: YouTubeQueue = YouTubeQueue()
        self._row_widgets: dict[YouTubeQueueItem, YouTubeQueueItemWidget] = {}
        self._list_items: dict[YouTubeQueueItem, QListWidgetItem] = {}

        # In-flight probe workers — kept alive until they finish.
        self._probe_workers: list[YouTubeProbeWorker] = []

        # Active queue worker. Public name preserved for
        # ``MainWindow.closeEvent`` which looks up ``getattr(tab, "worker_thread")``.
        self.worker_thread: YouTubeQueueWorker | None = None

        # Snapshot of the items handed to the active worker, in order.
        # Indexed by the worker's per-item idx signals; frozen at _start_run
        # so mid-run removals of COMPLETED rows don't shift the mapping.
        self._run_items: list[YouTubeQueueItem] = []

        # Curation bridge: the queue worker thread blocks on this event while
        # the GUI thread shows the curation dialog. Mirrors SingleEpisodeTab.
        self._curation_event = threading.Event()
        self._curation_result: list = []
        self._curation_requested.connect(self._on_curation_requested)

        self._setup_ui()
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
    # Add + probe lifecycle
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Add the current URL to the queue and spawn a probe worker."""
        if not self.add_button.isEnabled():
            return  # Defensive: returnPressed fires even when the button is disabled.
        url = self.url_edit.text().strip()
        if not url:
            return

        item = self._queue.add(url)
        # The queue model defaults to PENDING; flip to PROBING up-front so the
        # row widget renders the "(probing...)" hint immediately.
        item.status = YouTubeItemStatus.PROBING
        self._render_new_item(item)
        self.url_edit.clear()

        probe = YouTubeProbeWorker(self._fetcher, url, parent=self)
        probe.probe_done.connect(lambda info, it=item: self._on_probe_done(it, info))
        probe.probe_error.connect(lambda msg, it=item: self._on_probe_error(it, msg))
        probe.finished.connect(lambda pw=probe: self._on_probe_finished(pw))
        self._probe_workers.append(probe)
        probe.start()
        self._recompute_buttons()

    def _on_probe_done(self, item: YouTubeQueueItem, info: object) -> None:
        """Probe succeeded — classify the result and update the item."""
        if not isinstance(info, VideoInfo):  # pragma: no cover - signal guard
            self._mark_probe_error(item, "Invalid probe result.")
            return

        mineable, error, sub_mode = _classify_probe_result(info, self._config)
        if not mineable:
            item.video_info = info
            self._mark_probe_error(item, error or "Probe rejected.")
            return

        item.video_info = info
        item.video_id = info.video_id
        item.resolved_sub_mode = sub_mode
        item.error_message = None
        item.status = YouTubeItemStatus.READY
        self._refresh_row(item)
        self._recompute_buttons()

    def _on_probe_error(self, item: YouTubeQueueItem, message: str) -> None:
        """Probe failed — the item is unmineable."""
        self._mark_probe_error(item, message)

    def _mark_probe_error(self, item: YouTubeQueueItem, message: str) -> None:
        """Shared transition into PROBE_ERROR with consistent fields."""
        item.status = YouTubeItemStatus.PROBE_ERROR
        item.error_message = message
        self._refresh_row(item)
        self._recompute_buttons()

    def _on_probe_finished(self, probe: YouTubeProbeWorker) -> None:
        """Drop the probe handle once its QThread emits finished."""
        with contextlib.suppress(ValueError):
            self._probe_workers.remove(probe)

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

        # Snapshot BEFORE constructing the worker so all idx-based signal
        # handlers resolve against a frozen list that survives mid-run removals.
        self._run_items = list(ready_items)

        self.progress_widget.reset()

        worker = YouTubeQueueWorker(
            processor=self._processor,
            config=self._config,
            items=ready_items,
            curation_callback=self._curation_bridge,
            preview_mode=preview_mode,
        )
        worker.item_started.connect(self._on_item_started)
        worker.item_progress.connect(self._on_item_progress)
        worker.item_finished.connect(self._on_item_finished)
        worker.queue_finished.connect(self._on_queue_finished)
        self.worker_thread = worker

        mode_label = "Preview" if preview_mode else "Mine"
        self.log_widget.append_info(f"{mode_label} run starting — {len(ready_items)} items.")
        self._recompute_buttons()
        worker.start()

    def _on_stop_all_clicked(self) -> None:
        """Cancel the active run."""
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
        """Final signal — clear the worker handle and recompute buttons."""
        self.worker_thread = None
        self._run_items = []
        # Reset the stop button text in case we were cancelling.
        self.stop_button.setText("Stop All")
        self.stop_button.setEnabled(True)

        succeeded = sum(1 for i in self._queue.all_items() if i.status == YouTubeItemStatus.COMPLETED)
        failed = sum(1 for i in self._queue.all_items() if i.status == YouTubeItemStatus.ERROR)
        self.log_widget.append_info(f"Queue done: {succeeded} succeeded, {failed} failed.")
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
        # Collect targets first so we don't mutate during iteration.
        targets = [i for i in self._queue.all_items() if i.status != YouTubeItemStatus.PROCESSING]
        for item in targets:
            self._drop_item(item)
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

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh every button's enabled/visible state from the queue + worker.

        Derived from queue contents + worker handle:

        * Run active → Add/Preview/Mine disabled, Stop visible, Clear allowed.
        * Otherwise → Add enabled; Preview/Mine/Clear enabled iff a READY
          item exists; Stop hidden.
        """
        items = self._queue.all_items()
        has_items = bool(items)
        has_ready = any(i.status == YouTubeItemStatus.READY for i in items)
        run_active = self.worker_thread is not None

        self.add_button.setEnabled(not run_active)
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

    def _curation_bridge(self, words: list) -> list:
        """Thread-safe curation bridge: blocks the worker until the dialog closes.

        Called from the worker thread. Emits a signal to the GUI thread so the
        dialog runs on the correct thread, then blocks on ``_curation_event``
        until the GUI slot completes. Returns the user's selected words.
        """
        self._curation_event.clear()
        self._curation_result = []
        self._curation_requested.emit(words)
        self._curation_event.wait()
        return self._curation_result

    def _on_curation_requested(self, words: list) -> None:
        """Slot on the GUI thread that runs the curation dialog."""
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dialog = WordCurationDialog(words, self)
        if dialog.exec() == WordCurationDialog.DialogCode.Accepted:
            self._curation_result = dialog.get_selected_words()
        else:
            self._curation_result = []
        self._curation_event.set()

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

        worker_busy = self.worker_thread is not None and self.worker_thread.isRunning()
        if not worker_busy and self._presenter is not None:
            self._processor = create_episode_processor(
                config,
                self._presenter,
                stats_service=getattr(self._processor, "stats_service", None),
            )

    def shutdown(self) -> None:
        """Stop the active worker and tear down probe workers.

        Called by :class:`MainWindow` during closeEvent so that background
        threads don't outlive the application.
        """
        if self.worker_thread is not None:
            self.worker_thread.cancel()
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None

        for probe in list(self._probe_workers):
            probe.quit()
            probe.wait()
        self._probe_workers.clear()

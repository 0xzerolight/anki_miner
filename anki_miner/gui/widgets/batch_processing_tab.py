"""Enhanced batch processing tab with modern UI design."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.constants import MIN_HEIGHT_QUEUE_SECTION, SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import field_label_width, make_label_fit_text
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.panels import QueuePanel
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models.batch_queue import QueueItemStatus
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
    from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread
    from anki_miner.orchestration import EpisodeProcessor


logger = logging.getLogger(__name__)


class BatchProcessingTab(MiningTabBase):
    """Enhanced batch processing tab with modern UI design.

    Features:
    - Quick Processing section with FileSelector widgets
    - Multi-Series Queue via QueuePanel
    - Dual progress bars (overall + current episode)
    - Enhanced log widget
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback,
        stats_service=None,
        parent=None,
    ):
        """Initialize the batch processing tab.

        Args:
            config: Application configuration
            presenter: GUI presenter for output
            progress_callback: Progress callback for updates
            stats_service: Optional statistics recording service
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.stats_service = stats_service
        self.worker_thread: ManualPairWorkerThread | BatchQueueWorkerThread | None = None
        self._is_processing = False
        self._cancel_requested = False
        self._run_failed = False
        # Both start methods assign the same union-typed worker_thread, so the
        # active path (Queue = two-level series items vs Quick = one episode
        # per item) is tracked explicitly for _on_progress_update's branch.
        self._queue_mode = False
        self._items_done = 0
        self._items_total = 0
        self._current_item_label = ""

        # Initialize batch queue
        from anki_miner.models.batch_queue import BatchQueue

        self.batch_queue = BatchQueue()

        # Connect progress callback signals via shared base.
        self._wire_progress_callback(self.progress_callback)

        # Worker→GUI word-curation bridge (Issue #60).
        self._init_curation_bridge()

        self._setup_ui()

        # Enable drag-and-drop on the tab (subclass implements dragEnter/drop filtering).
        self._setup_drag_drop()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # Create scroll area for tab content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Create container widget for scroll area
        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Quick Processing Section
        quick_section = self._create_quick_processing_section()
        layout.addWidget(quick_section)

        # Multi-Series Queue Panel (extracted component)
        self.queue_panel = QueuePanel()
        self.queue_panel.process_requested.connect(self._process_queue)
        layout.addWidget(self.queue_panel, 1)  # Give it stretch factor

        # Issue #60: opt-in per-episode word curation popup (default off).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup for each episode before creating cards")
        )
        layout.addWidget(self.review_words_checkbox)

        # Overall Progress (for queue processing)
        overall_progress_header = QLabel(self.tr("Overall Progress"))
        overall_progress_header.setObjectName("heading3")
        font = QFont()
        font.setPixelSize(FONT_SIZES.body)
        font.setWeight(QFont.Weight.Bold)
        overall_progress_header.setFont(font)
        layout.addWidget(overall_progress_header)

        self.overall_progress_widget = ProgressWidget()
        layout.addWidget(self.overall_progress_widget)

        # Retry Failed button (hidden by default)
        self.retry_button = ModernButton(self.tr("Retry Failed"), variant="secondary")
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self._retry_failed_items)
        layout.addWidget(self.retry_button)

        # Log widget
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        # Connect presenter signals to log widget
        self.presenter.info_signal.connect(self.log_widget.append_info)
        self.presenter.success_signal.connect(self.log_widget.append_success)
        self.presenter.warning_signal.connect(self.log_widget.append_warning)
        self.presenter.error_signal.connect(self.log_widget.append_error)

        container.setLayout(layout)
        scroll_area.setWidget(container)

        # Main layout just holds the scroll area
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

        # Set up keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """Set up tab-specific keyboard shortcuts."""
        # Ctrl+O: Browse video folder
        browse_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        browse_shortcut.activated.connect(
            lambda: (self.video_folder_selector.browse() if hasattr(self, "video_folder_selector") else None)
        )

        # Ctrl+Return: Process queue
        process_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        process_shortcut.activated.connect(self._process_queue)

        # Ctrl+Shift+A: Add series to queue
        add_series_shortcut = QShortcut(QKeySequence("Ctrl+Shift+A"), self)
        add_series_shortcut.activated.connect(self.queue_panel.add_series_external)

    def _create_quick_processing_section(self) -> QFrame:
        """Create the quick processing section with card styling.

        Returns:
            Frame with quick processing controls
        """
        section = QFrame()
        section.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Section header
        header = SectionHeader(title=self.tr("Quick Processing"))
        layout.addWidget(header)

        # Shared label-column width so both folder rows and the offset row line up.
        label_w = field_label_width("Video Folder:", "Subtitle Folder:", "Subtitle Offset:")

        # Video folder selector
        self.video_folder_selector = FileSelector(
            label=self.tr("Video Folder:"), file_mode=False, file_filter="", label_width=label_w
        )
        layout.addWidget(self.video_folder_selector)

        # Subtitle folder selector
        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"), file_mode=False, file_filter="", label_width=label_w
        )
        layout.addWidget(self.subtitle_folder_selector)

        # Constant subtitle offset applied to every episode pair in the folder
        # (mirrors the Single Episode tab; per-session, seeded from config).
        offset_layout = QHBoxLayout()
        offset_layout.setSpacing(SPACING.xs)

        offset_label = QLabel(self.tr("Subtitle Offset:"))
        offset_label.setObjectName("field-label")
        offset_label.setFixedWidth(label_w)
        make_label_fit_text(offset_label)

        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        self.offset_spinbox.setSingleStep(0.5)
        self.offset_spinbox.setValue(self.config.subtitle_offset)
        self.offset_spinbox.setSuffix(self.tr(" seconds"))
        self.offset_spinbox.setToolTip(
            self.tr("Adjust subtitle timing for all episodes (positive = later, negative = earlier)")
        )

        offset_layout.addWidget(offset_label)
        offset_layout.addWidget(self.offset_spinbox)
        offset_layout.addStretch()
        layout.addLayout(offset_layout)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)

        self.process_pairs_button = ModernButton(self.tr("Process Folder"), variant="primary")
        self.process_pairs_button.clicked.connect(self._process_pairs)
        self.process_pairs_button.setToolTip(self.tr("Process every episode pair found in the selected folders"))
        button_layout.addWidget(self.process_pairs_button)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="danger")
        self.cancel_button.setToolTip(self.tr("Cancel processing"))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()
        button_layout.addWidget(self.cancel_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        section.setLayout(layout)

        # Set minimum height and size policy to prevent compression
        section.setMinimumHeight(MIN_HEIGHT_QUEUE_SECTION)
        section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        return section

    def _get_validated_folders(self) -> tuple[Path, Path] | None:
        """Validate and return folder paths from selectors.

        Returns:
            Tuple of (video_folder, subtitle_folder) or None if invalid
        """
        video_path = self.video_folder_selector.path_or_none()
        subtitle_path = self.subtitle_folder_selector.path_or_none()

        if video_path is None or subtitle_path is None:
            return None

        if not self.video_folder_selector.is_valid() or not self.subtitle_folder_selector.is_valid():
            return None

        return Path(video_path), Path(subtitle_path)

    def _find_episode_pairs(self, video_folder: Path, subtitle_folder: Path) -> list:
        """Find matching video/subtitle pairs in folders.

        Args:
            video_folder: Path to video folder
            subtitle_folder: Path to subtitle folder

        Returns:
            List of FilePair objects
        """
        from anki_miner.utils.file_pairing import FilePairMatcher

        return FilePairMatcher.find_pairs_by_episode_number(video_folder, subtitle_folder)

    def _process_pairs(self) -> None:
        """Process all discovered pairs from quick processing section."""
        if self._is_processing:
            return

        folders = self._get_validated_folders()
        if not folders:
            QMessageBox.warning(
                self, self.tr("Invalid Folders"), self.tr("Please select valid video and subtitle folders")
            )
            return

        video_folder, subtitle_folder = folders
        pairs = self._find_episode_pairs(video_folder, subtitle_folder)

        if not pairs:
            QMessageBox.warning(self, self.tr("No Pairs Found"), self.tr("No matching video/subtitle pairs found"))
            return

        self._start_processing_with_pairs(pairs)

    def _start_processing_with_pairs(self, pairs) -> None:
        """Start processing with manually paired files.

        Args:
            pairs: List of FilePair objects to process
        """
        # Clear log and reset the bar from the previous run's end state.
        self.log_widget.clear_log()
        self._begin_run(queue_mode=False)

        # Hide action buttons, show cancel
        self._is_processing = True
        self._show_cancel_state()

        # Log start
        self.presenter.show_info(tr_format(self.tr("Starting batch processing of %1 episodes..."), len(pairs)))

        # Tear down the previous run before building a new processor so leaked
        # sqlite handles / Session sockets can't survive into this run (Windows
        # back-to-back-mining freeze).
        self._teardown_previous_run("batch")

        # Process each pair sequentially in worker thread
        from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread

        # Read the constant offset on the GUI thread now; the factory runs on
        # the worker thread so it must close over the precomputed config, never
        # touch the spinbox (cross-thread QWidget access). Mirrors SingleEpisodeTab.
        config_with_offset = replace(self.config, subtitle_offset=self.offset_spinbox.value())

        # Pass a factory so the processor is built on the worker thread. This
        # keeps the GUI thread free during the slow registry scan, sqlite opens,
        # and CSV parses that happen during construction.
        def _processor_factory() -> EpisodeProcessor:
            return create_episode_processor(config_with_offset, self.presenter, self.stats_service)

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        self.worker_thread = ManualPairWorkerThread(
            None,
            pairs,
            self.progress_callback,
            curation_callback=curation_cb,
            processor_factory=_processor_factory,
        )

        # Pair-level signals set the counters/labels; the per-episode stage
        # sweep via progress_callback (wired in __init__) is composed into the
        # single overall bar by _on_progress_update.
        self.worker_thread.batch_started.connect(self._on_batch_started)
        self.worker_thread.pair_started.connect(self._on_pair_started)
        self.worker_thread.pair_finished.connect(self._on_pair_finished)
        self.worker_thread.result_ready.connect(self._on_processing_finished)
        self.worker_thread.error.connect(self._on_processing_error)
        self.worker_thread.finished.connect(self._restore_buttons)
        self.worker_thread.start()

    def _warn_incomplete_items(self) -> None:
        """Show warnings for incomplete queue items."""
        incomplete = self.queue_panel.get_incomplete_items()
        for widget, issue_type in incomplete:
            if issue_type == "invalid":
                QMessageBox.warning(
                    self,
                    self.tr("Invalid Folders"),
                    tr_format(self.tr("Series '%1' has folders that don't exist. Skipping."), widget.display_name),
                )
            else:
                QMessageBox.warning(
                    self,
                    self.tr("Incomplete Series"),
                    tr_format(self.tr("Series '%1' is missing folders. Skipping."), widget.display_name),
                )

    def _start_queue_worker(self) -> None:
        """Create and start the queue worker thread."""
        from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread

        # Tear down any prior run before building the queue worker (Windows
        # back-to-back-mining freeze: leaked sqlite/Session handles).
        self._teardown_previous_run("batch")

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        self.worker_thread = BatchQueueWorkerThread(
            self.batch_queue,
            self.config,
            self.presenter,
            self.progress_callback,
            stats_service=self.stats_service,
            curation_callback=curation_cb,
        )

        self.worker_thread.queue_started.connect(self._on_queue_started)
        self.worker_thread.item_started.connect(self._on_item_started)
        self.worker_thread.item_completed.connect(self._on_item_completed)
        self.worker_thread.item_failed.connect(self._on_item_failed)
        self.worker_thread.queue_finished.connect(self._on_queue_finished)
        # Run-level fatals (stale-dict gate, processor-build failure) emit
        # error THEN queue_finished — without the flag the terminal handler
        # would read "Complete — 0 cards created" on a failed run.
        self.worker_thread.error.connect(self._on_queue_worker_error)
        # Safety net (G1): restore the action buttons once the thread ends. The
        # quick (manual-pair) path already wires this; without it a caught
        # run-level failure (stale-dict gate, AnkiService construction) leaves
        # the buttons stranded in the running state.
        self.worker_thread.finished.connect(self._restore_buttons)

        self.worker_thread.start()

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Build (media_context, lookup_fn) from the live worker's current pair.

        The worker is blocked in ``_curation_event.wait()`` while this runs, so
        reading its ``_curation_*`` attributes is race-free.
        """
        w = self.worker_thread
        if w is None:
            return None, None
        media_context = self._make_curation_media_context(
            self.config, w._curation_video, w._curation_subtitle, offset=w._curation_offset
        )
        return media_context, self._lookup_fn_from_processor(w.curation_processor)

    def _process_queue(self) -> None:
        """Process all items in queue."""
        if self._is_processing:
            return

        valid_pairs = self.queue_panel.get_valid_pairs()

        if not valid_pairs:
            QMessageBox.information(self, self.tr("Empty Queue"), self.tr("No valid series in queue to process"))
            return

        self._warn_incomplete_items()

        # Populate batch queue from widgets (includes per-item subtitle offset).
        # Stamp each created QueueItem's id onto its source widget so status
        # and card-count updates from the worker address the right row, even
        # when two rows share a display_name (T-30).
        self.batch_queue.clear()
        for video_folder, subtitle_folder, display_name, subtitle_offset, widget in valid_pairs:
            item = self.batch_queue.add_item(video_folder, subtitle_folder, display_name, subtitle_offset)
            widget.item_id = item.id

        # Prepare UI for processing
        self._is_processing = True
        self.log_widget.clear_log()
        self._begin_run(queue_mode=True)
        self._show_cancel_state()
        self.presenter.show_info(
            tr_format(self.tr("Starting queue processing (%1 series)..."), self.batch_queue.pending_count)
        )

        # Start worker (creates processors per-item with subtitle offset)
        self._start_queue_worker()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        """Enable or disable all processing buttons.

        Args:
            enabled: Whether buttons should be enabled
        """
        self.process_pairs_button.setEnabled(enabled)
        self.queue_panel.set_buttons_enabled(enabled)

    def _show_cancel_state(self) -> None:
        """Hide action buttons and show cancel button."""
        self.process_pairs_button.hide()
        self.cancel_button.setText(self.tr("\u25a0 Cancel"))
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.queue_panel.set_buttons_enabled(False)

    def _restore_buttons(self) -> None:
        """Restore normal button state after processing ends."""
        self._is_processing = False
        self.cancel_button.hide()
        self.process_pairs_button.show()
        self._set_buttons_enabled(True)
        # Cancel recovery: the Quick-path worker suppresses result_ready on a
        # cancelled run, so QThread.finished (always fires) is the only safe
        # place to replace "Cancelling...". Idempotent for the queue path,
        # whose _on_queue_finished also handles the flag.
        if self._cancel_requested:
            self.overall_progress_widget.reset()
            self.overall_progress_widget.set_status(self.tr("Cancelled"))

    def _on_cancel_clicked(self) -> None:
        """Handle cancel button click."""
        self._cancel_requested = True
        # Release any open curation dialog first so the worker doesn't hang (Issue #60).
        self._cancel_active_curation_dialog()
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling..."))
        self.cancel_button.setEnabled(False)
        self.overall_progress_widget.set_status(self.tr("Cancelling..."))

    def _begin_run(self, queue_mode: bool) -> None:
        """Reset the bar, flags, and per-run counters at run start."""
        self.overall_progress_widget.reset()
        self._cancel_requested = False
        self._run_failed = False
        self._queue_mode = queue_mode
        self._items_done = 0
        self._items_total = 0
        self._current_item_label = ""

    def _on_queue_worker_error(self, message: str) -> None:
        """Run-level fatal from the queue worker: flag it and surface it."""
        self._run_failed = True
        self.presenter.show_error(message)

    def _on_queue_started(self, total_items: int) -> None:
        """Called when queue processing starts.

        Args:
            total_items: Total number of series to process
        """
        self._items_total = total_items
        self._items_done = 0
        self.overall_progress_widget.set_percent(0, self.tr("Starting queue processing..."))

    def _on_batch_started(self, total_pairs: int) -> None:
        """Quick Processing start: prime the Overall Progress bar with pair count.

        Mirrors :meth:`_on_queue_started` for the folder-pair path
        (ManualPairWorkerThread). The per-episode stage sweep via
        ``progress_callback`` is composed into the same bar.

        Args:
            total_pairs: Total number of episode pairs to process
        """
        self._items_total = total_pairs
        self._items_done = 0
        self.overall_progress_widget.set_percent(0, self.tr("Starting batch processing..."))

    def _on_pair_started(self, index: int, name: str) -> None:
        """Quick Processing per-pair start: refresh the persistent episode prefix.

        Args:
            index: 1-based pair index
            name: Display name (video file name)
        """
        self._current_item_label = tr_format(self.tr("Episode %1/%2: %3"), index, self._items_total, name)
        self.overall_progress_widget.set_status(self._current_item_label)

    def _on_pair_finished(self, completed: int, total: int) -> None:
        """Quick Processing per-pair tick: advance the composed bar.

        Args:
            completed: Number of pairs finished so far (1-based)
            total: Total number of pairs in the run
        """
        self._items_done = completed
        # Bar-only advance (no status): keeps the fill correct when a pair
        # errors mid-sweep; monotone with the composed per-episode updates.
        self.overall_progress_widget.set_composed(completed, 0, total)

    def _on_item_started(self, item_id: str, display_name: str) -> None:
        """Called when processing starts for an item.

        Render-only: the worker already set the item's status at pick time
        (it owns all QueueItem writes during a run — see
        BatchQueueWorkerThread.run). Writing status here raced the worker loop.

        Args:
            item_id: Item ID
            display_name: Display name of series
        """
        self.presenter.show_info(tr_format(self.tr("Processing series: %1"), display_name))
        self._current_item_label = tr_format(
            self.tr("Series %1/%2: %3"), self._items_done + 1, self._items_total, display_name
        )
        self.overall_progress_widget.set_status(self._current_item_label)
        self.queue_panel.set_item_status(item_id, "processing")

    def _on_item_completed(self, item_id: str, cards_created: int) -> None:
        """Called when an item completes successfully.

        Render-only: status/cards were already written by the worker before it
        emitted this signal (see BatchQueueWorkerThread.run), so completed_count
        below is accurate even while this slot lags the worker.

        Args:
            item_id: Item ID
            cards_created: Number of cards created
        """
        self._advance_queue_bar()
        self.presenter.show_success(tr_format(self.tr("Created %1 cards"), cards_created))

        # Update queue panel — address the completed row by id (T-30).
        self.queue_panel.set_processing_item_complete(item_id, cards_created)

    def _on_item_failed(self, item_id: str, error_message: str) -> None:
        """Called when an item fails.

        Render-only: the worker already set ERROR status and error_message
        before emitting (see BatchQueueWorkerThread.run).

        Args:
            item_id: Item ID
            error_message: Error message
        """
        self.presenter.show_error(error_message)
        self._advance_queue_bar()

        # Render the failed row with the error badge — the worker set the model
        # QueueItem's status but never drove the widget, so the row otherwise
        # stuck at "Processing" during the run and fell back to "Pending" after.
        self.queue_panel.set_item_status(item_id, "error")

    def _advance_queue_bar(self) -> None:
        """Advance the series-granular bar after a terminal item outcome.

        The queue path is TWO-LEVEL (one item = a series of N episodes whose
        count is unknown up front), so the bar moves per series only; the
        per-episode sweep drives the status label instead (see
        _on_progress_update).
        """
        self._items_done = self.batch_queue.completed_count + self.batch_queue.failed_count
        self.overall_progress_widget.set_composed(self._items_done, 0, self._items_total)

    def _on_queue_finished(self, total_cards: int) -> None:
        """Called when entire queue finishes.

        Args:
            total_cards: Total cards created across all series
        """
        self._restore_buttons()

        # Terminal end state: cancel -> failed -> success.
        if self._cancel_requested:
            self.overall_progress_widget.reset()
            self.overall_progress_widget.set_status(self.tr("Cancelled"))
        elif self._run_failed:
            self.overall_progress_widget.reset()
            self.overall_progress_widget.set_status(self.tr("Failed — see log"))
        else:
            self.overall_progress_widget.show_completion(tr_format(self.tr("Complete — %1 cards created"), total_cards))

        # Update queue stats
        self.queue_panel.update_stats()

        # A mid-pairs cancel returns its item to PENDING without emitting a
        # terminal signal, so its row can still read "processing" (set at
        # item-start). Re-sync every row from the worker-owned model status now
        # that the run is over — render-only and idempotent.
        _status_text = {
            QueueItemStatus.PENDING: "pending",
            QueueItemStatus.PROCESSING: "processing",
            QueueItemStatus.COMPLETED: "complete",
            QueueItemStatus.ERROR: "error",
        }
        for item in self.batch_queue.get_all_items():
            self.queue_panel.set_item_status(item.id, _status_text[item.status])

        # Show retry button if there are failed items that can be retried
        has_retryable = any(
            item.status == QueueItemStatus.ERROR and item.retry_count < item.max_retries
            for item in self.batch_queue.get_all_items()
        )
        self.retry_button.setVisible(has_retryable)

        # Show summary
        failed = self.batch_queue.failed_count
        summary = tr_format(
            self.tr("Processed %1 series\nTotal cards created: %2"), self.batch_queue.total_items, total_cards
        )
        if failed > 0:
            summary += tr_format(self.tr("\n%1 series failed"), failed)
        QMessageBox.information(self, self.tr("Queue Processing Complete"), summary)

    def _retry_failed_items(self) -> None:
        """Retry failed items in the batch queue."""
        if self._is_processing:
            return

        reset_count = self.batch_queue.reset_failed_for_retry()
        if reset_count == 0:
            QMessageBox.information(self, self.tr("No Items to Retry"), self.tr("No failed items eligible for retry."))
            self.retry_button.setVisible(False)
            return

        # Hide retry button and start processing. Use _show_cancel_state()
        # (not just _set_buttons_enabled(False)) so the Cancel button is
        # surfaced for the retry run, matching _process_queue and
        # _start_processing_with_pairs — otherwise the retry run is
        # uncancellable (T-22).
        self.retry_button.setVisible(False)
        self._is_processing = True
        self._begin_run(queue_mode=True)
        self._show_cancel_state()

        self.presenter.show_info(tr_format(self.tr("Retrying %1 failed items..."), reset_count))
        self._start_queue_worker()

    def _compose_status(self, item_description: str) -> str | None:
        """Glue the persistent item prefix onto the stage detail.

        The empty item_description from StageWeightedProgress.finish()'s
        terminal ``on_progress(100, "")`` shows the prefix alone — never a
        dangling "name — ".
        """
        if item_description and self._current_item_label:
            return f"{self._current_item_label} — {item_description}"
        if item_description:
            return item_description
        return self._current_item_label or None

    def _on_progress_start(self, total: int, description: str) -> None:
        """Per-episode stage start: status only (the composed bar never resets).

        Args:
            total: Total items in the stage (unused; the bar is composed)
            description: Stage description
        """
        status = self._compose_status(description)
        if status:
            self.overall_progress_widget.set_status(status)

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Per-episode stage sweep, path-dependent.

        Quick path (one episode per item): composed into the bar. Queue path
        (one SERIES per item, episode count unknown): status label only — the
        bar advances per series in _advance_queue_bar; composing the episode
        sweep against the series count would sawtooth.

        Args:
            current: Per-episode percent (StageWeightedProgress emits 0-100)
            item_description: Stage/item detail
        """
        status = self._compose_status(item_description)
        if self._queue_mode:
            if status:
                self.overall_progress_widget.set_status(status)
            return
        self.overall_progress_widget.set_composed(self._items_done, current, self._items_total, status)

    def _on_progress_complete(self) -> None:
        """Per-episode stage complete: no-op (terminal handlers own the summary)."""

    def _on_processing_finished(self, results: list) -> None:
        """Handle processing finished signal (for manual pair processing).

        Args:
            results: List of processing results
        """
        self._restore_buttons()

        # result_ready is suppressed on cancelled runs, so this is the
        # success-side terminal handler (cancel recovery is in
        # _restore_buttons); still guard against a late cancel race.
        if not self._cancel_requested:
            self.overall_progress_widget.show_completion(
                tr_format(self.tr("Complete — %1 cards created"), sum(r.cards_created for r in results))
            )

        # Show summary; failed episodes are returned as results with errors
        # populated (process_episode never raises), so count them explicitly
        # instead of presenting every finish as a success (Issue #51).
        total_cards = sum(r.cards_created for r in results)
        failed = sum(1 for r in results if not r.success)
        summary = tr_format(self.tr("Processed %1 episodes\nTotal cards created: %2"), len(results), total_cards)
        if failed > 0:
            summary += tr_format(self.tr("\n%1 episode(s) failed"), failed)
            QMessageBox.warning(self, self.tr("Batch Processing Complete"), summary)
        else:
            QMessageBox.information(self, self.tr("Batch Processing Complete"), summary)

    def _on_processing_error(self, error_message: str) -> None:
        """Handle processing error signal.

        Args:
            error_message: Error message
        """
        self._run_failed = True
        self._restore_buttons()

        # Show error
        self.presenter.show_error(error_message)

        # Reset progress
        self.overall_progress_widget.reset()
        self.overall_progress_widget.set_status(self.tr("Failed — see log"))

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept drag if any URL is a directory."""
        if event is None:
            return
        for url in urls_from_event(event):
            if Path(url.toLocalFile()).is_dir():
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Route dropped folders to the appropriate folder selector."""
        if event is None:
            return
        folders = [url.toLocalFile() for url in urls_from_event(event) if Path(url.toLocalFile()).is_dir()]
        if len(folders) >= 1:
            self.video_folder_selector.set_path(folders[0])
        if len(folders) >= 2:
            self.subtitle_folder_selector.set_path(folders[1])
        event.acceptProposedAction()

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration.

        Args:
            config: New configuration
        """
        # The Quick-path offset spinbox is a per-session value the user dials in
        # for the current folder batch; it is never persisted back to config.
        # Only follow config.subtitle_offset when the *persisted* value actually
        # changed, so an unrelated settings save / theme toggle (each of which
        # re-fires update_config) doesn't wipe the in-progress offset. Mirrors
        # SingleEpisodeTab.update_config.
        if config.subtitle_offset != self.config.subtitle_offset:
            self.offset_spinbox.setValue(config.subtitle_offset)
        self.config = config

    def release_dictionary_resources(self) -> bool:
        """Close sqlite handles cached by the most recent worker run.

        Both hosted workers (``ManualPairWorkerThread``,
        ``BatchQueueWorkerThread``) expose their retained processor via the
        typed ``curation_processor`` property. Either way, the handle is
        still open after the run finishes and blocks Settings → Remove /
        Re-import on Windows (Issue #30 follow-up).

        Returns ``False`` while a worker is actively running — closing
        providers under an in-flight processor would crash the run. The
        facade resets the chain so the next mine re-opens it cleanly.
        """
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return False
        if self.worker_thread is not None:
            proc = self.worker_thread.curation_processor
            if proc is not None:
                proc.release_dictionary_resources()
        return True

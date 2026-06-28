"""Enhanced batch processing tab with modern UI design."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
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
from anki_miner.gui.constants import MIN_HEIGHT_QUEUE_SECTION
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import field_label_width
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
        self._current_phase = ""

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

        # Current Episode Progress
        current_progress_header = QLabel(self.tr("Current Episode"))
        current_progress_header.setObjectName("heading3")
        current_progress_header.setFont(font)
        layout.addWidget(current_progress_header)

        self.current_progress_widget = ProgressWidget()
        layout.addWidget(self.current_progress_widget)

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

        # Ctrl+P: Preview/Scan pairs
        preview_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        preview_shortcut.activated.connect(lambda: self._process_pairs() if hasattr(self, "scan_button") else None)

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

        # Shared label-column width so both folder rows line up.
        label_w = field_label_width("Video Folder:", "Subtitle Folder:")

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

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)

        self.preview_pairs_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_pairs_button.clicked.connect(self._preview_pairs)
        self.preview_pairs_button.setToolTip(self.tr("Preview video/subtitle pairs before processing"))
        button_layout.addWidget(self.preview_pairs_button)

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
        video_path = self.video_folder_selector.get_path().strip()
        subtitle_path = self.subtitle_folder_selector.get_path().strip()

        if not video_path or not subtitle_path:
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

    def _preview_pairs(self) -> None:
        """Preview video/subtitle pairs before processing."""
        folders = self._get_validated_folders()
        if not folders:
            QMessageBox.warning(
                self, self.tr("Invalid Folders"), self.tr("Please select valid video and subtitle folders")
            )
            return

        video_folder, subtitle_folder = folders
        pairs = self._find_episode_pairs(video_folder, subtitle_folder)

        if not pairs:
            QMessageBox.warning(
                self,
                self.tr("No Pairs Found"),
                self.tr(
                    "No matching video/subtitle pairs found.\n\n"
                    "Files are paired by episode number, so point each folder at a "
                    "single show:\n"
                    "- episode_01.mp4 <-> episode_01.ass\n"
                    "- episode_02.mp4 <-> episode_02.ass\n\n"
                    "Mixing multiple shows in one folder can mispair episodes that "
                    "share a number — add each show as its own queue item."
                ),
            )
            return

        from anki_miner.gui.widgets.dialogs.pair_preview_dialog import PairPreviewDialog

        dialog = PairPreviewDialog(pairs, self)
        dialog.exec()

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
        # Clear log
        self.log_widget.clear_log()

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

        # Pass a factory so the processor is built on the worker thread. This
        # keeps the GUI thread free during the slow registry scan, sqlite opens,
        # and CSV parses that happen during construction.
        def _processor_factory() -> EpisodeProcessor:
            return create_episode_processor(self.config, self.presenter, self.stats_service)

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        self.worker_thread = ManualPairWorkerThread(
            None,
            pairs,
            self.progress_callback,
            curation_callback=curation_cb,
            processor_factory=_processor_factory,
        )

        # Drive the Overall Progress bar from the worker's pair-level signals;
        # the Current Episode bar is driven by the per-episode stage sweep via
        # progress_callback (wired in __init__), mirroring the queue path.
        self.worker_thread.batch_started.connect(self._on_batch_started)
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
        self.preview_pairs_button.setEnabled(enabled)
        self.process_pairs_button.setEnabled(enabled)
        self.queue_panel.set_buttons_enabled(enabled)

    def _show_cancel_state(self) -> None:
        """Hide action buttons and show cancel button."""
        self.preview_pairs_button.hide()
        self.process_pairs_button.hide()
        self.cancel_button.setText(self.tr("\u25a0 Cancel"))
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.queue_panel.set_buttons_enabled(False)

    def _restore_buttons(self) -> None:
        """Restore normal button state after processing ends."""
        self._is_processing = False
        self.cancel_button.hide()
        self.preview_pairs_button.show()
        self.process_pairs_button.show()
        self._set_buttons_enabled(True)

    def _on_cancel_clicked(self) -> None:
        """Handle cancel button click."""
        # Release any open curation dialog first so the worker doesn't hang (Issue #60).
        self._cancel_active_curation_dialog()
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling..."))
        self.cancel_button.setEnabled(False)
        self.current_progress_widget.set_status(self.tr("Cancelling..."))

    def _on_queue_started(self, total_items: int) -> None:
        """Called when queue processing starts.

        Args:
            total_items: Total number of series to process
        """
        self.overall_progress_widget.set_determinate(total_items)
        self.overall_progress_widget.set_progress(0, total_items, self.tr("Starting queue processing..."))

    def _on_batch_started(self, total_pairs: int) -> None:
        """Quick Processing start: prime the Overall Progress bar with pair count.

        Mirrors :meth:`_on_queue_started` for the folder-pair path
        (ManualPairWorkerThread). The Current Episode bar is driven separately
        by the per-episode stage sweep via ``progress_callback``.

        Args:
            total_pairs: Total number of episode pairs to process
        """
        self.overall_progress_widget.set_determinate(total_pairs)
        self.overall_progress_widget.set_progress(0, total_pairs, self.tr("Starting batch processing..."))

    def _on_pair_finished(self, completed: int, total: int) -> None:
        """Quick Processing per-pair tick: advance the Overall Progress bar.

        Args:
            completed: Number of pairs finished so far (1-based)
            total: Total number of pairs in the run
        """
        self.overall_progress_widget.set_progress(
            completed, total, tr_format(self.tr("Completed: %1/%2"), completed, total)
        )

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
        completed = self.batch_queue.completed_count
        total = self.batch_queue.total_items

        self.overall_progress_widget.set_progress(
            completed, total, tr_format(self.tr("Completed: %1/%2"), completed, total)
        )
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

    def _on_queue_finished(self, total_cards: int) -> None:
        """Called when entire queue finishes.

        Args:
            total_cards: Total cards created across all series
        """
        self._restore_buttons()

        # Reset progress bars to Ready state on any terminal path (cancel/success).
        self.current_progress_widget.reset()
        self.overall_progress_widget.reset()

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
        self._show_cancel_state()

        self.presenter.show_info(tr_format(self.tr("Retrying %1 failed items..."), reset_count))
        self._start_queue_worker()

    def _on_progress_start(self, total: int, description: str) -> None:
        """Handle progress start signal.

        Args:
            total: Total items
            description: Description
        """
        self._current_phase = description
        self.current_progress_widget.set_determinate(total)
        self.current_progress_widget.set_progress(0, total, description)

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Handle progress update signal.

        Args:
            current: Current progress value
            item_description: Description of current item
        """
        total = self.current_progress_widget.total
        self.current_progress_widget.set_progress(current, total, item_description)

    def _on_progress_complete(self) -> None:
        """Handle progress complete signal."""
        self.current_progress_widget.set_status(
            f"{self._current_phase} \u2014 done" if self._current_phase else "Complete"
        )

    def _on_processing_finished(self, results: list) -> None:
        """Handle processing finished signal (for manual pair processing).

        Args:
            results: List of processing results
        """
        self._restore_buttons()

        # Reset progress bars to Ready state on any terminal path (cancel/success).
        self.current_progress_widget.reset()
        self.overall_progress_widget.reset()

        # Show summary; failed episodes are returned as results with errors
        # populated (process_episode never raises), so count them explicitly
        # instead of presenting every finish as a success (Issue #51).
        total_cards = sum(r.cards_created for r in results)
        failed = sum(1 for r in results if not r.success)
        summary = tr_format(self.tr("Processed %1 episodes\nTotal cards created: %2"), len(results), total_cards)
        if failed > 0:
            summary += tr_format(self.tr("\n%1 episode(s) failed - see log for details"), failed)
            QMessageBox.warning(self, self.tr("Batch Processing Complete"), summary)
        else:
            QMessageBox.information(self, self.tr("Batch Processing Complete"), summary)

    def _on_processing_error(self, error_message: str) -> None:
        """Handle processing error signal.

        Args:
            error_message: Error message
        """
        self._restore_buttons()

        # Show error
        self.presenter.show_error(error_message)

        # Reset progress
        self.current_progress_widget.reset()
        self.overall_progress_widget.reset()

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

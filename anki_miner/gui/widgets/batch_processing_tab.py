"""Enhanced batch processing tab with modern UI design."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
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
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.panels import QueuePanel
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.models.batch_queue import QueueItemStatus

if TYPE_CHECKING:
    from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
    from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread


class BatchProcessingTab(QWidget):
    """Enhanced batch processing tab with modern UI design.

    Features:
    - Quick Processing section with FileSelector widgets
    - Multi-Anime Queue via QueuePanel
    - Dual progress bars (overall + current episode)
    - Enhanced log widget
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback,
        parent=None,
    ):
        """Initialize the batch processing tab.

        Args:
            config: Application configuration
            presenter: GUI presenter for output
            progress_callback: Progress callback for updates
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.worker_thread: ManualPairWorkerThread | BatchQueueWorkerThread | None = None
        self._is_processing = False
        self._current_phase = ""

        # Initialize batch queue
        from anki_miner.models.batch_queue import BatchQueue

        self.batch_queue = BatchQueue()

        # Connect progress callback signals
        self.progress_callback.start_signal.connect(self._on_progress_start)
        self.progress_callback.progress_signal.connect(self._on_progress_update)
        self.progress_callback.complete_signal.connect(self._on_progress_complete)
        self.progress_callback.error_signal.connect(self._on_progress_error)

        self._setup_ui()

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

        # Multi-Anime Queue Panel (extracted component)
        self.queue_panel = QueuePanel()
        self.queue_panel.process_requested.connect(self._process_queue)
        layout.addWidget(self.queue_panel, 1)  # Give it stretch factor

        # Overall Progress (for queue processing)
        overall_progress_header = QLabel("Overall Progress")
        overall_progress_header.setObjectName("heading3")
        font = QFont()
        font.setPixelSize(FONT_SIZES.body)
        font.setWeight(QFont.Weight.Bold)
        overall_progress_header.setFont(font)
        layout.addWidget(overall_progress_header)

        self.overall_progress_widget = ProgressWidget()
        layout.addWidget(self.overall_progress_widget)

        # Current Episode Progress
        current_progress_header = QLabel("Current Episode")
        current_progress_header.setObjectName("heading3")
        current_progress_header.setFont(font)
        layout.addWidget(current_progress_header)

        self.current_progress_widget = ProgressWidget()
        layout.addWidget(self.current_progress_widget)

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
        # Ctrl+O: Browse anime folder
        browse_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        browse_shortcut.activated.connect(
            lambda: (
                self.anime_folder_selector.browse()
                if hasattr(self, "anime_folder_selector")
                else None
            )
        )

        # Ctrl+P: Preview/Scan pairs
        preview_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        preview_shortcut.activated.connect(
            lambda: self._process_pairs() if hasattr(self, "scan_button") else None
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
        header = SectionHeader(
            title="Quick Processing", icon="video", action_text="", action_icon=""
        )
        layout.addWidget(header)

        # Anime folder selector
        self.anime_folder_selector = FileSelector(
            label="Anime Folder:", file_mode=False, file_filter=""
        )
        layout.addWidget(self.anime_folder_selector)

        # Subtitle folder selector
        self.subtitle_folder_selector = FileSelector(
            label="Subtitle Folder:", file_mode=False, file_filter=""
        )
        layout.addWidget(self.subtitle_folder_selector)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)

        self.preview_pairs_button = ModernButton("Preview", icon="preview", variant="secondary")
        self.preview_pairs_button.clicked.connect(self._preview_pairs)
        self.preview_pairs_button.setToolTip("Preview video/subtitle pairs before processing")
        button_layout.addWidget(self.preview_pairs_button)

        self.process_pairs_button = ModernButton(
            "Process All Pairs", icon="play", variant="primary"
        )
        self.process_pairs_button.clicked.connect(self._process_pairs)
        self.process_pairs_button.setToolTip("Process all discovered episode pairs")
        button_layout.addWidget(self.process_pairs_button)

        self.cancel_button = ModernButton("Cancel", icon="stop", variant="danger")
        self.cancel_button.setToolTip("Cancel processing")
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
            Tuple of (anime_folder, subtitle_folder) or None if invalid
        """
        anime_path = self.anime_folder_selector.get_path().strip()
        subtitle_path = self.subtitle_folder_selector.get_path().strip()

        if not anime_path or not subtitle_path:
            return None

        if (
            not self.anime_folder_selector.is_valid()
            or not self.subtitle_folder_selector.is_valid()
        ):
            return None

        return Path(anime_path), Path(subtitle_path)

    def _find_episode_pairs(self, anime_folder: Path, subtitle_folder: Path) -> list:
        """Find matching video/subtitle pairs in folders.

        Args:
            anime_folder: Path to anime folder
            subtitle_folder: Path to subtitle folder

        Returns:
            List of FilePair objects
        """
        from anki_miner.utils.file_pairing import FilePairMatcher

        return FilePairMatcher.find_pairs_by_episode_number(anime_folder, subtitle_folder)

    def _preview_pairs(self) -> None:
        """Preview video/subtitle pairs before processing."""
        folders = self._get_validated_folders()
        if not folders:
            QMessageBox.warning(
                self, "Invalid Folders", "Please select valid anime and subtitle folders"
            )
            return

        anime_folder, subtitle_folder = folders
        pairs = self._find_episode_pairs(anime_folder, subtitle_folder)

        if not pairs:
            QMessageBox.warning(
                self,
                "No Pairs Found",
                "No matching video/subtitle pairs found.\n\n"
                "Ensure files have matching base names:\n"
                "- episode_01.mp4 <-> episode_01.ass\n"
                "- episode_02.mp4 <-> episode_02.ass",
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
                self, "Invalid Folders", "Please select valid anime and subtitle folders"
            )
            return

        anime_folder, subtitle_folder = folders
        pairs = self._find_episode_pairs(anime_folder, subtitle_folder)

        if not pairs:
            QMessageBox.warning(self, "No Pairs Found", "No matching video/subtitle pairs found")
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
        self.presenter.show_info(f"Starting batch processing of {len(pairs)} episodes...")

        # Create episode processor using service factory
        episode_processor = create_episode_processor(self.config, self.presenter)

        # Process each pair sequentially in worker thread
        from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread

        self.worker_thread = ManualPairWorkerThread(
            episode_processor, pairs, self.progress_callback
        )

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
                    "Invalid Folders",
                    f"Series '{widget.display_name}' has folders that don't exist. Skipping.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Incomplete Series",
                    f"Series '{widget.display_name}' is missing folders. Skipping.",
                )

    def _start_queue_worker(self) -> None:
        """Create and start the queue worker thread."""
        from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread

        self.worker_thread = BatchQueueWorkerThread(
            self.batch_queue, self.config, self.presenter, self.progress_callback
        )

        self.worker_thread.queue_started.connect(self._on_queue_started)
        self.worker_thread.item_started.connect(self._on_item_started)
        self.worker_thread.item_completed.connect(self._on_item_completed)
        self.worker_thread.item_failed.connect(self._on_item_failed)
        self.worker_thread.queue_finished.connect(self._on_queue_finished)

        self.worker_thread.start()

    def _process_queue(self) -> None:
        """Process all items in queue."""
        if self._is_processing:
            return

        valid_pairs = self.queue_panel.get_valid_pairs()

        if not valid_pairs:
            QMessageBox.information(self, "Empty Queue", "No valid series in queue to process")
            return

        self._warn_incomplete_items()

        # Populate batch queue from widgets (includes per-item subtitle offset)
        self.batch_queue.clear()
        for anime_folder, subtitle_folder, display_name, subtitle_offset in valid_pairs:
            self.batch_queue.add_item(anime_folder, subtitle_folder, display_name, subtitle_offset)

        # Prepare UI for processing
        self._is_processing = True
        self.log_widget.clear_log()
        self._show_cancel_state()
        self.presenter.show_info(
            f"Starting queue processing ({self.batch_queue.pending_count} series)..."
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
        self.cancel_button.setText("\u25a0 Cancel")
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
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText("Cancelling...")
        self.cancel_button.setEnabled(False)
        self.current_progress_widget.set_status("Cancelling...")

    def _on_queue_started(self, total_items: int) -> None:
        """Called when queue processing starts.

        Args:
            total_items: Total number of series to process
        """
        self.overall_progress_widget.set_determinate(total_items)
        self.overall_progress_widget.set_progress(0, total_items, "Starting queue processing...")

    def _on_item_started(self, item_id: str, display_name: str) -> None:
        """Called when processing starts for an item.

        Args:
            item_id: Item ID
            display_name: Display name of series
        """
        # Update model state from GUI thread
        items = self.batch_queue.get_all_items()
        for item in items:
            if item.id == item_id:
                item.status = QueueItemStatus.PROCESSING
                break

        self.presenter.show_info(f"Processing series: {display_name}")
        self.queue_panel.set_item_status(display_name, "processing")

    def _on_item_completed(self, item_id: str, cards_created: int) -> None:
        """Called when an item completes successfully.

        Args:
            item_id: Item ID
            cards_created: Number of cards created
        """
        # Update model state from GUI thread
        items = self.batch_queue.get_all_items()
        for item in items:
            if item.id == item_id:
                item.status = QueueItemStatus.COMPLETED
                item.cards_created = cards_created
                break

        completed = self.batch_queue.completed_count
        total = self.batch_queue.total_items

        self.overall_progress_widget.set_progress(
            completed, total, f"Completed: {completed}/{total}"
        )
        self.presenter.show_success(f"Created {cards_created} cards")

        # Update queue panel
        self.queue_panel.set_processing_item_complete(cards_created)

    def _on_item_failed(self, item_id: str, error_message: str) -> None:
        """Called when an item fails.

        Args:
            item_id: Item ID
            error_message: Error message
        """
        # Update model state from GUI thread
        items = self.batch_queue.get_all_items()
        for item in items:
            if item.id == item_id:
                item.status = QueueItemStatus.ERROR
                item.error_message = error_message
                break

        self.presenter.show_error(f"Error: {error_message}")

    def _on_queue_finished(self, total_cards: int) -> None:
        """Called when entire queue finishes.

        Args:
            total_cards: Total cards created across all series
        """
        self._restore_buttons()

        # Update queue stats
        self.queue_panel.update_stats()

        # Show summary
        self.overall_progress_widget.set_status("Queue processing complete")
        QMessageBox.information(
            self,
            "Queue Processing Complete",
            f"Processed {self.batch_queue.total_items} anime series\n"
            f"Total cards created: {total_cards}",
        )

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

    def _on_progress_error(self, item: str, error: str) -> None:
        """Handle per-item error from progress callback.

        Args:
            item: Description of the failed item
            error: Error message
        """
        self.log_widget.append_error(f"Failed: {item} \u2014 {error}")

    def _on_processing_finished(self, results: list) -> None:
        """Handle processing finished signal (for manual pair processing).

        Args:
            results: List of processing results
        """
        self._restore_buttons()

        # Show summary
        total_cards = sum(r.cards_created for r in results)
        QMessageBox.information(
            self,
            "Batch Processing Complete",
            f"Processed {len(results)} episodes\n" f"Total cards created: {total_cards}",
        )

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

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration.

        Args:
            config: New configuration
        """
        self.config = config

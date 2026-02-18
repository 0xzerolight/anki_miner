"""Single episode mining tab for GUI."""

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.constants import (
    SUBTITLE_FILE_FILTER,
    SUBTITLE_OFFSET_MAX,
    SUBTITLE_OFFSET_MIN,
    VIDEO_FILE_FILTER,
)
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.recent_files import RecentFilesManager
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets.base import configure_expanding_container, make_label_fit_text
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".m4v", ".mov"}
SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa"}


class SingleEpisodeTab(QWidget):
    """Tab for processing a single episode.

    This tab allows users to select a video and subtitle file, adjust subtitle
    offset, and process the episode to mine vocabulary and create Anki cards.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback,
        parent=None,
    ):
        """Initialize the single episode tab.

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
        self.worker_thread: EpisodeWorkerThread | None = None
        self._is_processing = False
        self._current_phase = ""
        self.recent_manager = RecentFilesManager()

        # Connect progress callback signals
        self.progress_callback.start_signal.connect(self._on_progress_start)
        self.progress_callback.progress_signal.connect(self._on_progress_update)
        self.progress_callback.complete_signal.connect(self._on_progress_complete)
        self.progress_callback.error_signal.connect(self._on_progress_error)

        self._setup_ui()

        # Enable drag-and-drop on the tab
        self.setAcceptDrops(True)

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

        # File selection section with card styling
        file_group = self._create_file_selection_group()
        layout.addWidget(file_group)

        # Actions section
        from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader

        actions_header = SectionHeader("Actions", icon="play")
        layout.addWidget(actions_header)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.xs)

        self.preview_button = ModernButton("Preview Words", icon="preview", variant="secondary")
        self.preview_button.setToolTip("Preview discovered words before creating cards")
        self.process_button = ModernButton("Process Episode", icon="play", variant="primary")
        self.process_button.setToolTip("Create Anki cards from the episode")

        self.preview_button.clicked.connect(self._on_preview_clicked)
        self.process_button.clicked.connect(self._on_process_clicked)

        button_layout.addWidget(self.preview_button)
        button_layout.addWidget(self.process_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Progress section
        progress_header = SectionHeader("Progress", icon="stats")
        layout.addWidget(progress_header)

        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

        # Log widget (already has its own header and styling)
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
        # Ctrl+O: Browse video file
        browse_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        browse_shortcut.activated.connect(self.video_selector.browse)

        # Ctrl+P: Preview words
        preview_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        preview_shortcut.activated.connect(self._on_preview_clicked)

        # Ctrl+Return: Process episode
        process_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        process_shortcut.activated.connect(self._on_process_clicked)

        # Update button tooltips to show shortcuts
        self.preview_button.setToolTip("Preview discovered words before creating cards (Ctrl+P)")
        self.process_button.setToolTip("Create Anki cards from the episode (Ctrl+Enter)")

        # Set accessibility properties
        self._setup_accessibility()

    def _setup_accessibility(self) -> None:
        """Set up accessibility features for screen readers."""
        self.setAccessibleName("Episode Mining Tab")
        self.setAccessibleDescription(
            "Process a single anime episode to create vocabulary flashcards"
        )

        # Set proper tab order: video selector -> subtitle selector -> offset -> preview -> process
        self.setTabOrder(self.video_selector, self.subtitle_selector)
        self.setTabOrder(self.subtitle_selector, self.offset_spinbox)
        self.setTabOrder(self.offset_spinbox, self.preview_button)
        self.setTabOrder(self.preview_button, self.process_button)

    def _create_file_selection_group(self) -> QFrame:
        """Create file selection group with enhanced file selectors.

        Returns:
            Frame with file selection controls
        """
        from anki_miner.gui.widgets.enhanced import FileSelector, SectionHeader

        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Section header
        header = SectionHeader("File Selection", icon="folder")
        layout.addWidget(header)

        # Recent files dropdown
        recent_layout = QHBoxLayout()
        recent_layout.setSpacing(SPACING.xs)
        recent_label = QLabel("Recent Files:")
        recent_label.setObjectName("field-label")
        recent_label.setMinimumWidth(100)
        make_label_fit_text(recent_label)
        recent_layout.addWidget(recent_label)

        self.recent_combo = QComboBox()
        self.recent_combo.addItem("Select recent file pair...")
        self.recent_combo.currentIndexChanged.connect(self._on_recent_selected)
        recent_layout.addWidget(self.recent_combo, 1)
        layout.addLayout(recent_layout)

        self._refresh_recent_combo()

        # Video file selector
        self.video_selector = FileSelector(
            label="Video File:", file_mode=True, file_filter=VIDEO_FILE_FILTER
        )
        layout.addWidget(self.video_selector)

        # Subtitle file selector
        self.subtitle_selector = FileSelector(
            label="Subtitle File:", file_mode=True, file_filter=SUBTITLE_FILE_FILTER
        )
        layout.addWidget(self.subtitle_selector)

        # Subtitle offset with helper text
        offset_layout = QHBoxLayout()
        offset_layout.setSpacing(SPACING.xs)

        offset_label = QLabel("Subtitle Offset:")
        offset_label.setObjectName("field-label")
        offset_label.setMinimumWidth(100)
        make_label_fit_text(offset_label)

        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        self.offset_spinbox.setSingleStep(0.5)
        self.offset_spinbox.setValue(self.config.subtitle_offset)
        self.offset_spinbox.setSuffix(" seconds")
        self.offset_spinbox.setToolTip(
            "Adjust subtitle timing (positive = later, negative = earlier)"
        )

        offset_layout.addWidget(offset_label)
        offset_layout.addWidget(self.offset_spinbox)
        offset_layout.addStretch()
        layout.addLayout(offset_layout)

        # Add spacing before helper text
        layout.addSpacing(4)

        # Helper text
        helper_label = QLabel("Adjust if subtitles are out of sync")
        helper_label.setObjectName("helper-text")
        helper_label.setWordWrap(True)  # Allow text to wrap if needed
        from PyQt6.QtGui import QFont

        from anki_miner.gui.resources.styles import FONT_SIZES

        helper_font = QFont()
        helper_font.setPixelSize(FONT_SIZES.small)
        helper_label.setFont(helper_font)
        layout.addWidget(helper_label)

        group.setLayout(layout)

        # Allow the group to expand/contract with its content
        configure_expanding_container(group)

        return group

    def _on_preview_clicked(self) -> None:
        """Handle preview button click."""
        self._start_processing(preview_mode=True)

    def _on_process_clicked(self) -> None:
        """Handle process button click."""
        self._start_processing(preview_mode=False)

    def _start_processing(self, preview_mode: bool) -> None:
        """Start episode processing.

        Args:
            preview_mode: If True, only preview words without creating cards
        """
        if self._is_processing:
            return

        # Validate inputs using FileSelector validation
        video_path = self.video_selector.get_path().strip()
        subtitle_path = self.subtitle_selector.get_path().strip()

        if not video_path or not subtitle_path:
            QMessageBox.warning(
                self, "Missing Files", "Please select both video and subtitle files"
            )
            return

        if not self.video_selector.is_valid():
            QMessageBox.warning(self, "File Not Found", f"Video file not found: {video_path}")
            return

        if not self.subtitle_selector.is_valid():
            QMessageBox.warning(self, "File Not Found", f"Subtitle file not found: {subtitle_path}")
            return

        video_file = Path(video_path)
        subtitle_file = Path(subtitle_path)

        # Update config with subtitle offset
        offset = self.offset_spinbox.value()
        config_with_offset = replace(self.config, subtitle_offset=offset)

        # Clear log
        self.log_widget.clear_log()

        # Disable buttons and set processing flag
        self._is_processing = True
        self.preview_button.setEnabled(False)
        self.process_button.setEnabled(False)

        # Create processor using service factory
        processor = create_episode_processor(config_with_offset, self.presenter)

        # Create and start worker thread
        self.worker_thread = EpisodeWorkerThread(
            processor, video_file, subtitle_file, preview_mode, self.progress_callback
        )

        self.worker_thread.result_ready.connect(self._on_processing_finished)
        self.worker_thread.error.connect(self._on_processing_error)
        self.worker_thread.start()

    def _on_progress_start(self, total: int, description: str) -> None:
        """Handle progress start signal.

        Args:
            total: Total number of items
            description: Operation description
        """
        self._current_phase = description
        self.progress_widget.set_determinate(total)
        self.progress_widget.set_status(description)

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Handle progress update signal.

        Args:
            current: Current item number
            item_description: Description of current item
        """
        self.progress_widget.set_value(current)
        self.progress_widget.set_status(item_description)

    def _on_progress_complete(self) -> None:
        """Handle progress complete signal."""
        self.progress_widget.set_status(
            f"{self._current_phase} \u2014 done" if self._current_phase else "Complete"
        )

    def _on_progress_error(self, item: str, error: str) -> None:
        """Handle per-item error from progress callback.

        Args:
            item: Description of the failed item
            error: Error message
        """
        self.log_widget.append_error(f"Failed: {item} \u2014 {error}")

    def _on_processing_finished(self, result) -> None:
        """Handle processing finished signal.

        Args:
            result: ProcessingResult object
        """
        # Re-enable buttons
        self._is_processing = False
        self.preview_button.setEnabled(True)
        self.process_button.setEnabled(True)

        # Add to recent files
        video_path = self.video_selector.get_path().strip()
        subtitle_path = self.subtitle_selector.get_path().strip()
        if video_path and subtitle_path:
            self.recent_manager.add_entry(Path(video_path), Path(subtitle_path))
            self._refresh_recent_combo()

        # Show result
        self.presenter.show_processing_result(result)

    def _on_processing_error(self, error_message: str) -> None:
        """Handle processing error signal.

        Args:
            error_message: Error message
        """
        # Re-enable buttons
        self._is_processing = False
        self.preview_button.setEnabled(True)
        self.process_button.setEnabled(True)

        # Show error
        self.presenter.show_error(error_message)

        # Reset progress
        self.progress_widget.reset()

    def _refresh_recent_combo(self) -> None:
        """Refresh the recent files combo box from disk."""
        self.recent_combo.blockSignals(True)
        self.recent_combo.clear()
        self.recent_combo.addItem("Select recent file pair...")

        entries = self.recent_manager.get_recent()
        for entry in entries:
            video_name = Path(entry["video"]).name
            subtitle_name = Path(entry["subtitle"]).name
            self.recent_combo.addItem(
                f"{video_name} + {subtitle_name}",
                userData=entry,
            )

        self.recent_combo.blockSignals(False)

    def _on_recent_selected(self, index: int) -> None:
        """Handle recent file selection from combo box.

        Args:
            index: Selected combo box index (0 = placeholder)
        """
        if index <= 0:
            return

        entry = self.recent_combo.itemData(index)
        if entry:
            self.video_selector.set_path(entry["video"])
            self.subtitle_selector.set_path(entry["subtitle"])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept drag if files have video or subtitle extensions."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                suffix = Path(url.toLocalFile()).suffix.lower()
                if suffix in VIDEO_EXTENSIONS or suffix in SUBTITLE_EXTENSIONS:
                    event.acceptProposedAction()
                    return

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Accept drag move events."""
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        """Route dropped files to the appropriate file selector."""
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            suffix = Path(file_path).suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                self.video_selector.set_path(file_path)
            elif suffix in SUBTITLE_EXTENSIONS:
                self.subtitle_selector.set_path(file_path)
        event.acceptProposedAction()

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration.

        Args:
            config: New configuration
        """
        self.config = config
        self.offset_spinbox.setValue(config.subtitle_offset)

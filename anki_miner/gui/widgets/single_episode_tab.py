"""Single episode mining tab for GUI."""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
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
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.utils.recent_files import RecentFilesManager
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import configure_expanding_container, make_label_fit_text
from anki_miner.gui.widgets.dialogs import AudioTracksDialog
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils import list_audio_streams
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".m4v", ".mov"}
SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa"}


class SingleEpisodeTab(MiningTabBase):
    """Tab for processing a single episode.

    This tab allows users to select a video and subtitle file, adjust subtitle
    offset, and process the episode to mine vocabulary and create Anki cards.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback,
        stats_service=None,
        parent=None,
    ):
        """Initialize the single episode tab.

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
        self.worker_thread: EpisodeWorkerThread | None = None
        self._is_processing = False
        self._current_phase = ""
        self.recent_manager = RecentFilesManager()
        self._audio_track_override: int | None = None

        self._init_curation_bridge()

        # Connect progress callback signals via shared base.
        self._wire_progress_callback(self.progress_callback)

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

        # File selection section with card styling
        file_group = self._create_file_selection_group()
        layout.addWidget(file_group)

        # Reset audio-track override when the video file changes
        self.video_selector.path_changed.connect(self._on_video_path_changed)

        # Actions section
        from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader

        actions_header = SectionHeader("Actions")
        layout.addWidget(actions_header)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.xs)

        self.preview_button = ModernButton("Preview Words", variant="secondary")
        self.preview_button.setToolTip("Preview discovered words before creating cards")
        self.process_button = ModernButton("Process Episode", variant="primary")
        self.process_button.setToolTip("Create Anki cards from the episode")
        self.timing_button = ModernButton("Test Timing", variant="secondary")
        self.timing_button.setToolTip("Preview video with subtitles to adjust timing offset")
        self.tracks_button = ModernButton("Tracks", variant="secondary")
        self.tracks_button.setToolTip("Manually choose which audio track to use for this episode")

        self.cancel_button = ModernButton("Cancel", variant="danger")
        self.cancel_button.setToolTip("Cancel processing")
        self.cancel_button.hide()

        self.preview_button.clicked.connect(self._on_preview_clicked)
        self.process_button.clicked.connect(self._on_process_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.timing_button.clicked.connect(self._on_timing_clicked)
        self.tracks_button.clicked.connect(self._on_tracks_clicked)

        button_layout.addWidget(self.preview_button)
        button_layout.addWidget(self.process_button)
        button_layout.addWidget(self.timing_button)
        button_layout.addWidget(self.tracks_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Progress section
        progress_header = SectionHeader("Progress")
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
        self.setAccessibleDescription("Process a single anime episode to create vocabulary flashcards")

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
        header = SectionHeader("File Selection")
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
        # Bound the combo's minimum width so long recent-file names cannot drive
        # the file-selection card (and the Expanding progress bar/log) wider than
        # the window (Issue #56). The default AdjustToContentsOnFirstShow makes
        # minimumSizeHint content-driven, pinning the layout to the widest item.
        self.recent_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.recent_combo.setMinimumContentsLength(20)
        self.recent_combo.addItem("Select recent file pair...")
        self.recent_combo.currentIndexChanged.connect(self._on_recent_selected)
        recent_layout.addWidget(self.recent_combo, 1)
        layout.addLayout(recent_layout)

        self._refresh_recent_combo()

        # Video file selector
        self.video_selector = FileSelector(label="Video File:", file_mode=True, file_filter=VIDEO_FILE_FILTER)
        layout.addWidget(self.video_selector)

        # Subtitle file selector
        self.subtitle_selector = FileSelector(label="Subtitle File:", file_mode=True, file_filter=SUBTITLE_FILE_FILTER)
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
        self.offset_spinbox.setToolTip("Adjust subtitle timing (positive = later, negative = earlier)")

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

    def _on_video_path_changed(self, _new_path: str) -> None:
        """Reset the audio-track override when the video file changes.

        Selection is per-run and must not silently carry over across files.
        """
        self._audio_track_override = None

    def _on_tracks_clicked(self) -> None:
        """Open the AudioTracksDialog for manual audio track override selection."""
        video_path = self.video_selector.get_path().strip()
        if not video_path:
            QMessageBox.warning(self, "Missing Video File", "Select a video file first.")
            return
        if not self.video_selector.is_valid():
            QMessageBox.warning(self, "File Not Found", f"Video file not found: {video_path}")
            return

        video_file = Path(video_path)

        # Probe (cached transparently by MediaExtractorService is per-extractor; here
        # we probe directly for the dialog). Each click probes fresh — cheap for
        # typical anime files (<1s).
        streams = list_audio_streams(video_file, ffprobe_cmd=resolve_ffprobe(self.config))

        if not streams:
            QMessageBox.information(
                self,
                "No Audio Tracks",
                "No audio tracks detected. Check that ffprobe is installed and the file has audio.",
            )
            return

        # Resolve the auto-detected pick so the dialog can show it in the "Auto" radio.
        auto_stream = next(
            (s for s in streams if s.language_tag in JAPANESE_LANGUAGE_CODES),
            None,
        )

        dialog = AudioTracksDialog(
            streams=streams,
            current_override=self._audio_track_override,
            auto_detected=auto_stream,
            parent=self,
        )
        if dialog.exec() == AudioTracksDialog.DialogCode.Accepted:
            self._audio_track_override = dialog.selected_override()

    def _on_preview_clicked(self) -> None:
        """Handle preview button click."""
        self._start_processing(preview_mode=True)

    def _on_process_clicked(self) -> None:
        """Handle process button click."""
        self._start_processing(preview_mode=False)

    def _on_timing_clicked(self) -> None:
        """Handle test timing button click. Opens the subtitle viewer dialog."""
        video_path = self.video_selector.get_path().strip()
        subtitle_path = self.subtitle_selector.get_path().strip()

        if not video_path or not subtitle_path:
            QMessageBox.warning(self, "Missing Files", "Select both video and subtitle files.")
            return

        if not self.video_selector.is_valid():
            QMessageBox.warning(self, "File Not Found", f"Video file not found: {video_path}")
            return

        if not self.subtitle_selector.is_valid():
            QMessageBox.warning(self, "File Not Found", f"Subtitle file not found: {subtitle_path}")
            return

        video_file = Path(video_path)
        subtitle_file = Path(subtitle_path)

        # Parse raw subtitle entries
        try:
            offset = self.offset_spinbox.value()
            # Parse with zero offset — SubtitleViewer handles offsetting itself
            config_no_offset = replace(self.config, subtitle_offset=0.0)
            parser = SubtitleParserService(config_no_offset)
            entries = parser.parse_raw_entries(subtitle_file)
        except Exception as e:
            logger.error("Failed to parse subtitles: %s", e)
            QMessageBox.critical(self, "Parse Error", "Failed to parse subtitles. Check the file format.")
            return

        if not entries:
            QMessageBox.information(self, "No Subtitles", "No subtitle entries found in the file.")
            return

        # Open subtitle viewer
        from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

        viewer = SubtitleViewer(
            video_file,
            entries,
            initial_offset=offset,
            parent=self,
            audio_track_override=self._audio_track_override,
            ffprobe_cmd=resolve_ffprobe(self.config),
        )
        if viewer.exec() == SubtitleViewer.DialogCode.Accepted:
            self.offset_spinbox.setValue(viewer.get_offset())

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
            QMessageBox.warning(self, "Missing Files", "Select both video and subtitle files.")
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

        # Hide action buttons, show cancel button
        self._is_processing = True
        self.preview_button.hide()
        self.process_button.hide()
        self.timing_button.hide()
        self.tracks_button.hide()
        self.cancel_button.setText("\u25a0 Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()

        # Create processor using service factory
        processor = create_episode_processor(config_with_offset, self.presenter, self.stats_service)

        # Create and start worker thread
        curation_cb = self._curation_bridge if not preview_mode else None
        self.worker_thread = EpisodeWorkerThread(
            processor,
            video_file,
            subtitle_file,
            preview_mode,
            self.progress_callback,
            curation_callback=curation_cb,
            audio_track_override=self._audio_track_override,
        )

        self.worker_thread.result_ready.connect(self._on_processing_finished)
        self.worker_thread.error.connect(self._on_processing_error)
        self.worker_thread.finished.connect(self._restore_buttons)
        self.worker_thread.start()

    # Progress slots (_on_progress_start/update/complete) are inherited from
    # MiningTabBase, which drives the single ``progress_widget`` via the
    # percentage-scaled ``set_progress`` path.

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Build (media_context, lookup_fn) from this tab's selectors + live worker."""
        media_context: CurationMediaContext | None = None
        video_path = self.video_selector.get_path().strip()
        subtitle_path = self.subtitle_selector.get_path().strip()
        if video_path and subtitle_path:
            try:
                offset = self.offset_spinbox.value()
                config_no_offset = replace(self.config, subtitle_offset=0.0)
                parser = SubtitleParserService(config_no_offset)
                entries = parser.parse_raw_entries(Path(subtitle_path))
                media_context = CurationMediaContext(
                    video_file=Path(video_path),
                    subtitle_entries=entries,
                    offset=offset,
                    audio_track_override=self._audio_track_override,
                    ffprobe_cmd=resolve_ffprobe(self.config),
                )
            except Exception:
                logger.exception("Failed to build media context for curation; proceeding without player")
                media_context = None

        lookup_fn = None
        if self.worker_thread is not None:
            proc = getattr(self.worker_thread, "processor", None)
            if proc is not None:
                lookup_fn = proc.definition_service.lookup_all_offline
        return media_context, lookup_fn

    def _on_cancel_clicked(self) -> None:
        """Handle cancel button click."""
        self._cancel_active_curation_dialog()
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText("Cancelling...")
        self.cancel_button.setEnabled(False)
        self.progress_widget.set_status("Cancelling...")

    def _restore_buttons(self) -> None:
        """Restore normal button state after processing ends."""
        self._is_processing = False
        self.cancel_button.hide()
        self.preview_button.show()
        self.process_button.show()
        self.timing_button.show()
        self.tracks_button.show()

    def _on_processing_finished(self, result) -> None:
        """Handle processing finished signal.

        Args:
            result: ProcessingResult object
        """
        self._restore_buttons()

        # Add to recent files
        video_path = self.video_selector.get_path().strip()
        subtitle_path = self.subtitle_selector.get_path().strip()
        if video_path and subtitle_path:
            offset = self.offset_spinbox.value()
            self.recent_manager.add_entry(Path(video_path), Path(subtitle_path), offset)
            self._refresh_recent_combo()

        # Clear file selectors so the next run starts from a clean slate.
        self.video_selector.clear()
        self.subtitle_selector.clear()

        # Show result
        self.presenter.show_processing_result(result)

        # Reset per-run override so next Process uses Auto unless user picks again
        self._audio_track_override = None

    def _on_processing_error(self, error_message: str) -> None:
        """Handle processing error signal.

        Args:
            error_message: Error message
        """
        self._restore_buttons()

        # Show error
        self.presenter.show_error(error_message)

        # Reset progress
        self.progress_widget.reset()

        # Reset per-run override on error path too
        self._audio_track_override = None

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
            self.offset_spinbox.setValue(entry.get("subtitle_offset", 0.0))

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept drag if files have video or subtitle extensions."""
        if event is None:
            return
        for url in urls_from_event(event):
            suffix = Path(url.toLocalFile()).suffix.lower()
            if suffix in VIDEO_EXTENSIONS or suffix in SUBTITLE_EXTENSIONS:
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Route dropped files to the appropriate file selector."""
        if event is None:
            return
        for url in urls_from_event(event):
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

    def release_dictionary_resources(self) -> bool:
        """Close sqlite handles cached by the most recent worker run.

        The processor is created fresh per run, but the finished worker
        retains it on ``self.worker_thread.processor`` until a new run
        replaces ``self.worker_thread``. On Windows those cached handles
        keep ``index.sqlite`` locked, so Settings → Remove / Re-import fails
        after the user has mined at least once (Issue #30 follow-up).

        Returns ``False`` while a worker is actively running — closing
        providers under an in-flight processor would crash the run.
        ``DefinitionService.close()`` resets ``_loaded`` so the next mine
        re-opens the chain cleanly.
        """
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return False
        if self.worker_thread is not None:
            proc = getattr(self.worker_thread, "processor", None)
            if proc is not None:
                proc.definition_service.close()
        return True

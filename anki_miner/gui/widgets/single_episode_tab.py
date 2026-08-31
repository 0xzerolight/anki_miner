"""Single episode mining tab for GUI."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.constants import (
    SUBTITLE_FILE_FILTER,
    SUBTITLE_OFFSET_MAX,
    SUBTITLE_OFFSET_MIN,
    VIDEO_FILE_FILTER,
)
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.keyboard_shortcuts import scoped_shortcut
from anki_miner.gui.utils.qt_helpers import reveal_settings, urls_from_event
from anki_miner.gui.utils.recent_files import RecentFilesManager
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    cap_row_field,
    configure_card_layout,
    configure_expanding_container,
    field_label_width,
    make_label_fit_text,
)
from anki_miner.gui.widgets.dialogs import AudioTracksDialog
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.orchestration.episode_processor import sanitize_source_label
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils import list_audio_streams
from anki_miner.utils.audio_track_detector import matches_language_tag
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.file_pairing import FilePairMatcher, find_sibling_subtitle
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.widgets.subtitles_tab import SubtitlesTab
    from anki_miner.orchestration import EpisodeProcessor
    from anki_miner.utils.audio_track_detector import AudioStream

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".m4v", ".mov"}
#: Drop routing reuses the pairing set so a subtitle this screen accepts on drop
#: is always one the matcher can pair. VIDEO_EXTENSIONS stays local: the drop
#: target splits video from subtitle, and the two sets must stay disjoint.
SUBTITLE_EXTENSIONS = FilePairMatcher.SUBTITLE_EXTENSIONS


class SingleEpisodeTab(MiningTabBase):
    """Tab for processing a single episode.

    This tab allows users to select a video and subtitle file, adjust subtitle
    offset, and process the episode to mine vocabulary and create Anki cards.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the pinned
    #: bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "run.single"
    TASK_OWNER = CapabilityTarget("video", "single")

    # Test-only seam: emitted synchronously (same-thread DIRECT connection) with
    # the freshly built worker JUST BEFORE ``.start()`` so a test driver can
    # connect capture slots to the worker before run() can emit. Dormant in
    # normal use — the real app never connects, so the emit is a no-op.
    worker_created = pyqtSignal(object)  # EpisodeWorkerThread

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
        self._cancel_requested = False
        self.recent_manager = RecentFilesManager()
        self._audio_track_override: int | None = None
        # Bumped in shutdown() so a Tracks/Timing probe callback already queued
        # for delivery when app close begins finds itself stale and never
        # touches a button the close may be tearing down (M7).
        self._teardown_generation = 0

        # Run snapshots — captured on the GUI thread at _start_processing so
        # completion and off-thread curation never read mutable QWidgets for run
        # inputs. Keep raw strings too: Path normalizes spellings such as ``./x``,
        # but selector ownership is the exact text present when the run started.
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_video_raw: str | None = None
        self._curation_subtitle_raw: str | None = None
        self._curation_offset: float = 0.0
        self._curation_audio_track_override: int | None = None

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

        # Timing and Tracks stay here beside the fields they act on. Process
        # Episode and Cancel are moved into the pinned bar below (D6), so the
        # one action this screen exists for cannot scroll off it.
        actions_header = SectionHeader(self.tr("Actions"))
        layout.addWidget(actions_header)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.xs)

        self.process_button = ModernButton(self.tr("Process Episode"), variant="primary")
        self.process_button.setToolTip(self.tr("Create Anki cards from the episode"))
        self.timing_button = ModernButton(self.tr("Test Timing"), variant="secondary")
        self.timing_button.setToolTip(self.tr("Preview video with subtitles to adjust timing offset"))
        self.tracks_button = ModernButton(self.tr("Tracks"), variant="secondary")
        self.tracks_button.setToolTip(self.tr("Manually choose which audio track to use for this episode"))

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setToolTip(self.tr("Cancel processing"))
        self.cancel_button.hide()

        self.process_button.clicked.connect(self._on_process_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.timing_button.clicked.connect(self._on_timing_clicked)
        self.tracks_button.clicked.connect(self._on_tracks_clicked)

        button_layout.addWidget(self.timing_button)
        button_layout.addWidget(self.tracks_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Progress section
        progress_header = SectionHeader(self.tr("Progress"))
        layout.addWidget(progress_header)

        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)
        # The durable end state of this same card (D20). One episode per run,
        # so the receipt never needs a noun to count.
        self._install_receipt(layout, self.progress_widget)

        # Carries its own header and styling; install_workflow_shell moves it into the Activity drawer (D6).
        self.log_widget = LogWidget()

        # Connect presenter signals to log widget
        self.presenter.info_signal.connect(self.log_widget.append_info)
        self.presenter.success_signal.connect(self.log_widget.append_success)
        self.presenter.warning_signal.connect(self.log_widget.append_warning)
        self.presenter.error_signal.connect(self.log_widget.append_error)

        container.setLayout(layout)

        # Scroll, Activity drawer, pinned bar (D6). The log moves out of the
        # scrolled column into the drawer, so it costs nothing until it is
        # opened.
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._install_action_bar(
            main_layout,
            scroll_area,
            container,
            self.PAGE_WIDTH,
            primary=self.process_button,
            secondary=(self.cancel_button,),
            log=self.log_widget,
        )
        self.setLayout(main_layout)
        self.install_issue_banner(main_layout)

        # Set up keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """Set up tab-specific keyboard shortcuts.

        Ctrl+O only. Ctrl+Enter is installed by ``_install_action_bar``, which
        routes it through the pinned primary button; the copy that used to live
        here called ``_on_process_clicked`` directly, so it ignored whether the
        button was enabled and could start a second run over a first.
        """
        # Ctrl+O: browse for the video. Scoped, because Batch owns Ctrl+O too
        # and both pages live in one window -- unscoped, the hidden one could win.
        scoped_shortcut(self, QKeySequence("Ctrl+O"), self.video_selector.browse)

        # Set accessibility properties
        self._setup_accessibility()

    def _setup_accessibility(self) -> None:
        """Set up accessibility features for screen readers."""
        self.setAccessibleName(self.tr("Episode Mining Tab"))
        self.setAccessibleDescription(self.tr("Process a single video episode to create vocabulary flashcards"))

        # Tab order through the page's own inputs: video -> subtitle -> source -> offset.
        #
        # It deliberately stops there. Process Episode used to be chained on the
        # end, and that was right while the button sat in the form; D6 moved it
        # into the pinned action bar at the foot of the screen, so the old line
        # pulled focus from the offset field straight down to the bar and back
        # up again for Test Timing and Tracks. The bar is laid out in reading
        # order and comes last in the page, so leaving it alone is what puts the
        # primary action where the eye already expects it -- last.
        self.setTabOrder(self.video_selector, self.subtitle_selector)
        self.setTabOrder(self.subtitle_selector, self.card_source_edit)
        self.setTabOrder(self.card_source_edit, self.offset_spinbox)

    def _create_file_selection_group(self) -> QFrame:
        """Create file selection group with enhanced file selectors.

        Returns:
            Frame with file selection controls
        """
        from anki_miner.gui.widgets.enhanced import FileSelector, SectionHeader

        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        # Section header
        header = SectionHeader(self.tr("File Selection"))
        layout.addWidget(header)

        # Shared label-column width so every labeled row in this card lines its
        # input field up at the same x.
        # Measure the TRANSLATED strings: the labels below render via self.tr(),
        # so sizing the column on the English literals hard-clipped every
        # non-English locale (German needed 274px in a 105px box).
        label_w = field_label_width(
            self.tr("Recent Files:"),
            self.tr("Video File:"),
            self.tr("Subtitle File:"),
            self.tr("Card Source:"),
            self.tr("Subtitle Offset:"),
        )

        # Recent files dropdown
        recent_layout = QHBoxLayout()
        recent_layout.setSpacing(SPACING.xs)
        recent_label = QLabel(self.tr("Recent Files:"))
        recent_label.setObjectName("field-label")
        recent_label.setMinimumWidth(label_w)
        make_label_fit_text(recent_label)
        recent_layout.addWidget(recent_label)

        self.recent_combo = QComboBox()
        # Bound the combo's minimum width so long recent-file names cannot drive
        # the file-selection card (and the Expanding progress bar/log) wider than
        # the window (Issue #56). The default AdjustToContentsOnFirstShow makes
        # minimumSizeHint content-driven, pinning the layout to the widest item.
        self.recent_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.recent_combo.setMinimumContentsLength(20)
        self.recent_combo.addItem(self.tr("Select recent file pair..."))
        self.recent_combo.currentIndexChanged.connect(self._on_recent_selected)
        recent_layout.addWidget(self.recent_combo, 1)
        # Hand-built row, so it needs the row cap the FileSelectors below get
        # from their own constructor; without it this one control would stretch
        # to the full page column while the file rows under it stopped short.
        cap_row_field(self.recent_combo, label_w, recent_layout.spacing())
        recent_layout.addStretch()
        layout.addLayout(recent_layout)

        self._refresh_recent_combo()

        # Video file selector
        self.video_selector = FileSelector(
            label=self.tr("Video File:"),
            file_mode=True,
            file_filter=VIDEO_FILE_FILTER,
            label_width=label_w,
            history_key="video.single.inputs",
        )
        layout.addWidget(self.video_selector)

        # Subtitle file selector
        self.subtitle_selector = FileSelector(
            label=self.tr("Subtitle File:"),
            file_mode=True,
            file_filter=SUBTITLE_FILE_FILTER,
            label_width=label_w,
            history_key="video.single.inputs",
        )
        layout.addWidget(self.subtitle_selector)

        source_layout = QHBoxLayout()
        source_layout.setSpacing(SPACING.xs)
        source_label = QLabel(self.tr("Card Source:"))
        source_label.setObjectName("field-label")
        source_label.setMinimumWidth(label_w)
        make_label_fit_text(source_label)

        self.card_source_edit = QLineEdit()
        self.card_source_edit.setPlaceholderText(self.tr("Video title shown on cards"))
        self.card_source_edit.setToolTip(
            self.tr("Source title stored on cards; changing it does not change analytics grouping")
        )
        self.card_source_edit.setAccessibleName(self.tr("Card source"))
        source_label.setBuddy(self.card_source_edit)

        source_layout.addWidget(source_label)
        source_layout.addWidget(self.card_source_edit)
        cap_row_field(self.card_source_edit, label_w, source_layout.spacing())
        source_layout.addStretch()
        layout.addLayout(source_layout)

        # Subtitle offset with helper text
        offset_layout = QHBoxLayout()
        offset_layout.setSpacing(SPACING.xs)

        offset_label = QLabel(self.tr("Subtitle Offset:"))
        offset_label.setObjectName("field-label")
        offset_label.setMinimumWidth(label_w)
        make_label_fit_text(offset_label)

        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        self.offset_spinbox.setSingleStep(0.5)
        self.offset_spinbox.setValue(self.config.subtitle_offset)
        self.offset_spinbox.setSuffix(self.tr(" seconds"))
        self.offset_spinbox.setToolTip(self.tr("Adjust subtitle timing (positive = later, negative = earlier)"))

        offset_layout.addWidget(offset_label)
        offset_layout.addWidget(self.offset_spinbox)
        offset_layout.addStretch()
        layout.addLayout(offset_layout)

        group.setLayout(layout)

        # Allow the group to expand/contract with its content
        configure_expanding_container(group)

        return group

    def _on_video_path_changed(self, new_path: str) -> None:
        """Reset the audio-track override when the video file changes.

        Selection is per-run and must not silently carry over across files.
        Also auto-fills the subtitle selector from a sibling subtitle when the
        selector is currently empty and a matching sibling exists.
        """
        self._audio_track_override = None

        if not new_path:
            self.card_source_edit.clear()
            return

        self.card_source_edit.setText(sanitize_source_label(Path(new_path).stem))

        if self.subtitle_selector.get_path().strip():
            # User already has a subtitle chosen — don't overwrite it.
            return

        sibling = find_sibling_subtitle(Path(new_path))
        if sibling is not None:
            self.subtitle_selector.set_path(str(sibling))

    def _open_media_settings(self) -> None:
        """Repair action for a probe failure: the ffmpeg/ffprobe paths live there."""
        reveal_settings(self, "media")

    def _on_tracks_clicked(self) -> None:
        """Open the AudioTracksDialog for manual audio track override selection."""
        # Not a fresh attempt (D24): opening the picker must not clear a real
        # run failure still on screen.
        video_path = self.video_selector.path_or_none()
        if video_path is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a video file first.")))
            return
        if not self.video_selector.is_valid():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That video file no longer exists."), details=video_path)
            )
            return

        video_file = Path(video_path)
        ffprobe_cmd = resolve_ffprobe(self.config)

        # Probe off the GUI thread — ffprobe on a large file can block long
        # enough to freeze the UI. Disable the button so a second click can't
        # spawn a parallel probe; re-enabled in both callbacks.
        self.tracks_button.setEnabled(False)
        generation = self._teardown_generation

        def _probe() -> object:
            # Each click probes fresh — cheap for typical anime files (<1s).
            return list_audio_streams(video_file, ffprobe_cmd=ffprobe_cmd)

        def _on_streams(result: object) -> None:
            if generation != self._teardown_generation:
                return
            # Tab torn down while the probe was in flight (its C++ widgets are
            # gone); the queued callback has nothing live to update.
            with contextlib.suppress(RuntimeError):
                self.tracks_button.setEnabled(True)
                if self.video_selector.path_or_none() != video_path:
                    return
                streams = cast("list[AudioStream]", result)
                if not streams:
                    QMessageBox.information(
                        self,
                        self.tr("No Audio Tracks"),
                        self.tr("No audio tracks detected. Check that ffprobe is installed and the file has audio."),
                    )
                    return

                # Resolve the auto-detected pick so the dialog can show it in the "Auto" radio.
                codes = get_profile(config_language(self.config)).audio_track_codes
                auto_stream = next(
                    (s for s in streams if matches_language_tag(s.language_tag, codes)),
                    None,
                )

                dialog = AudioTracksDialog(
                    streams=streams,
                    current_override=self._audio_track_override,
                    auto_detected=auto_stream,
                    parent=self,
                )
                if dialog.exec() == AudioTracksDialog.DialogCode.Accepted:
                    if self.video_selector.path_or_none() != video_path:
                        return
                    self._audio_track_override = dialog.selected_override()

        def _on_probe_error(msg: str) -> None:
            logger.error("Failed to probe audio tracks: %s", msg)
            if generation != self._teardown_generation:
                return
            with contextlib.suppress(RuntimeError):
                self.tracks_button.setEnabled(True)
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr("Audio tracks could not be read."),
                        details=msg,
                        action_id="settings.media",
                        action_text=self.tr("Open Media Settings"),
                    ),
                    action=self._open_media_settings,
                )

        run_off_thread(self, _probe, _on_streams, _on_probe_error)

    def _on_process_clicked(self) -> None:
        """Handle process button click."""
        self._start_processing()

    def _on_timing_clicked(self) -> None:
        """Handle test timing button click. Opens the subtitle viewer dialog."""
        # Not a fresh attempt (D24): opening the timing probe must not clear a
        # real run failure still on screen.
        video_path = self.video_selector.path_or_none()
        subtitle_path = self.subtitle_selector.path_or_none()

        if video_path is None or subtitle_path is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose both a video file and a subtitle file.")))
            return

        if not self.video_selector.is_valid():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That video file no longer exists."), details=video_path)
            )
            return

        if not self.subtitle_selector.is_valid():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That subtitle file no longer exists."), details=subtitle_path)
            )
            return

        video_file = Path(video_path)
        subtitle_file = Path(subtitle_path)
        offset = self.offset_spinbox.value()

        # Parse off the GUI thread — a large subtitle can take ~1s and would
        # otherwise freeze the UI. Disable the button so a second click can't
        # spawn a parallel parse; re-enabled in both callbacks.
        # Parse with zero offset — SubtitleViewer handles offsetting itself.
        config_no_offset = replace(self.config, subtitle_offset=0.0)
        self.timing_button.setEnabled(False)
        generation = self._teardown_generation

        def _parse() -> object:
            return SubtitleParserService(config_no_offset).parse_raw_entries(subtitle_file)

        def _on_parsed(result: object) -> None:
            if generation != self._teardown_generation:
                return
            # Tab torn down while the parse was in flight (its C++ widgets are
            # gone); the queued callback has nothing live to update.
            with contextlib.suppress(RuntimeError):
                self.timing_button.setEnabled(True)
                if (
                    self.video_selector.path_or_none() != video_path
                    or self.subtitle_selector.path_or_none() != subtitle_path
                ):
                    return
                entries = cast("list[tuple[float, float, str]]", result)
                if not entries:
                    QMessageBox.information(
                        self, self.tr("No Subtitles"), self.tr("No subtitle entries found in the file.")
                    )
                    return

                # Open subtitle viewer
                from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

                viewer = SubtitleViewer(
                    video_file,
                    entries,
                    initial_offset=offset,
                    parent=self,
                    audio_track_override=self._audio_track_override,
                    audio_track_codes=get_profile(config_language(self.config)).audio_track_codes,
                    content_style=get_profile(config_language(self.config)).content_style,
                )
                # Nothing happens until exec() returns: the viewer holds a live mpv
                # core and releases it on the way out, so navigating (or writing the
                # offset) before then would race its teardown.
                result = viewer.exec()
                if result == SubtitleViewer.DialogCode.Accepted:
                    self.offset_spinbox.setValue(viewer.get_offset())
                elif result == SubtitleViewer.ALIGN_REQUESTED:
                    self._hand_off_to_retime(video_file, subtitle_file)

        def _on_parse_error(msg: str) -> None:
            logger.error("Failed to parse subtitles: %s", msg)
            if generation != self._teardown_generation:
                return
            with contextlib.suppress(RuntimeError):
                self.timing_button.setEnabled(True)
                self.show_screen_issue(
                    ScreenIssue(summary=self.tr("The subtitles could not be read. Check the file format."), details=msg)
                )

        run_off_thread(self, _parse, _on_parsed, _on_parse_error)

    def _subtitles_container(self) -> SubtitlesTab | None:
        """The Subtitles tab that owns Retime, or None if this tab is unhosted.

        A stripped shell (tests, a future embedding) has no Subtitles tab, and a
        hand-off that cannot land is a quiet no-op rather than a crash.
        """
        from anki_miner.gui.widgets.subtitles_tab import SubtitlesTab

        window = self.window()
        return None if window is None else window.findChild(SubtitlesTab)

    def _hand_off_to_retime(self, video_file: Path, subtitle_file: Path) -> None:
        """Take the user to the automatic aligner with this pair loaded (D35).

        The timing viewer aligns nothing itself; "Align automatically" closes it
        and lands here. Navigation goes through the window's own capability
        routing so this tab never learns a tab index.
        """
        from anki_miner.gui.capabilities import CapabilityTarget

        container = self._subtitles_container()
        if container is None:
            logger.warning("No Subtitles tab to hand the alignment off to")
            return

        reveal = getattr(self.window(), "reveal_capability", None)
        if callable(reveal):
            reveal(CapabilityTarget("subtitles", "retime"))
        container.open_retime(video_file, subtitle_file)

    def _start_processing(self) -> None:
        """Start episode processing."""
        if self._is_processing:
            return

        # A fresh attempt supersedes the complaint about the last one. After the
        # reentrancy guard, before the checks that re-raise whatever is still
        # wrong.
        self.clear_screen_issue()

        # Validate inputs using FileSelector validation
        video_path = self.video_selector.path_or_none()
        subtitle_path = self.subtitle_selector.path_or_none()

        if video_path is None or subtitle_path is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose both a video file and a subtitle file.")))
            return

        if not self.video_selector.is_valid():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That video file no longer exists."), details=video_path)
            )
            return

        if not self.subtitle_selector.is_valid():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That subtitle file no longer exists."), details=subtitle_path)
            )
            return

        video_file = Path(video_path)
        subtitle_file = Path(subtitle_path)
        source_label = self.card_source_edit.text().strip() or sanitize_source_label(video_file.stem)
        if not source_label:
            source_label = video_file.stem.strip()

        # Update config with subtitle offset
        offset = self.offset_spinbox.value()
        config_with_offset = replace(self.config, subtitle_offset=offset)

        # Snapshot the selector values on the GUI thread so the off-thread
        # _build_curation_context reads plain attributes, never live QWidgets.
        self._curation_video = video_file
        self._curation_subtitle = subtitle_file
        self._curation_video_raw = video_path
        self._curation_subtitle_raw = subtitle_path
        self._curation_offset = offset
        self._curation_audio_track_override = self._audio_track_override

        # Clear log and reset the bar from the previous run's end state
        # (success leaves the bar pinned at 100% with a summary).
        self.log_widget.clear_log()
        self.progress_widget.reset()
        self._cancel_requested = False
        # One episode per run, so the receipt counts notes and never items.
        self._begin_receipt(1)

        # Hide action buttons, show cancel button
        self._is_processing = True
        self.process_button.hide()
        self.timing_button.hide()
        self.tracks_button.hide()
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()

        # Tear down the previous run's worker + processor BEFORE starting a new
        # one. A fresh processor is created per run and its sqlite handles /
        # requests.Session were never released; on Windows those leak and
        # collide with subsequent GUI-thread service construction, hard-freezing
        # the app on back-to-back single-episode mines. Join the old worker,
        # then close its processor so no stale handle survives into the new run.
        self._teardown_previous_run("single-episode")

        # Pass a factory so the processor is built on the worker thread.
        # This keeps the GUI thread free during the slow registry scan,
        # sqlite opens, and CSV parses that happen during construction.
        # DEBUG-logged so a Windows reporter running with debug logging can
        # confirm the GUI-thread build no longer blocks.
        def _processor_factory() -> EpisodeProcessor:
            logger.debug("building processor for %s (worker thread)", video_file)
            proc = create_episode_processor(config_with_offset, self.presenter, self.stats_service)
            logger.debug("processor built for %s (worker thread)", video_file)
            return proc

        self._publish_task_start(self.tr("Single episode"))

        # Create and start worker thread
        curation_cb = self._curation_bridge
        self.worker_thread = EpisodeWorkerThread(
            None,
            video_file,
            subtitle_file,
            progress_callback=self.progress_callback,
            curation_callback=curation_cb,
            audio_track_override=self._audio_track_override,
            # Card source is presentation only. Keep file-derived series/episode
            # identities untouched so existing analytics groups do not split.
            source_label_override=source_label,
            processor_factory=_processor_factory,
        )

        self.worker_thread.result_ready.connect(self._on_processing_finished)
        self.worker_thread.error.connect(self._on_processing_error)
        self.worker_thread.finished.connect(self._restore_buttons)
        # Seal the receipt on the thread's own end, which is emitted after
        # run() returns: by then the result (or the error) has been delivered,
        # including the committed-notes result a cancelled run still produces.
        self.worker_thread.finished.connect(self._on_run_thread_finished)
        # Test seam: let any listener attach to the worker BEFORE it starts (so a
        # connect-before-start cannot miss an immediate emit). No-op in normal use.
        self.worker_created.emit(self.worker_thread)
        self.worker_thread.start()

    # Progress slots (_on_progress_start/update/complete) are inherited from
    # MiningTabBase, which drives the single ``progress_widget`` via the
    # percentage-scaled ``set_progress`` path.

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Build (media_context, lookup_fn) from GUI-thread snapshots + live worker.

        Runs off the GUI thread (dispatched by ``MiningTabBase._on_curation_requested``),
        so it reads the ``_curation_*`` snapshots captured at ``_start_processing``
        rather than the live selector QWidgets (cross-thread QWidget access is UB).
        The only tab that passes a real ``audio_track_override`` — the per-run
        Tracks-dialog pick must carry into the curation player.
        """
        media_context = self._make_curation_media_context(
            self.config,
            self._curation_video,
            self._curation_subtitle,
            offset=self._curation_offset,
            audio_track_override=self._curation_audio_track_override,
        )
        proc = self.worker_thread.curation_processor if self.worker_thread is not None else None
        return media_context, self._lookup_fn_from_processor(proc)

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._on_cancel_clicked()

    def _on_cancel_clicked(self) -> None:
        """Cancel the run: one verb, no prompt, and no invented progress after it.

        The bar freezes where it truly was rather than continuing towards a
        finish that will not happen, and the button states plainly that the
        request has been made and is being waited on.
        """
        self._cancel_requested = True
        self._publish_task_cancelling()
        self._cancel_active_curation_dialog()
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling…"))
        self.cancel_button.setEnabled(False)
        self.progress_widget.freeze()
        self.progress_widget.set_status(self.tr("Cancelling…"))

    def _restore_buttons(self) -> None:
        """Restore normal button state after processing ends."""
        self._is_processing = False
        self.cancel_button.hide()
        self.process_button.show()
        self.timing_button.show()
        self.tracks_button.show()
        # Cancel recovery lives HERE (QThread.finished always fires), not in
        # the result slot: the worker suppresses result_ready on a cancelled
        # run (and on curation reject), so "Cancelling…" would otherwise be
        # stranded forever.
        if self._cancel_requested:
            # Deliberately no reset(): zeroing the bar at the end of a cancel
            # erases how far the run actually got, which is the one thing the
            # user wants to know when they stop something.
            self.progress_widget.set_status(self.tr("Cancelled"))

    def _on_processing_finished(self, result) -> None:
        """Handle processing finished signal.

        Args:
            result: ProcessingResult object
        """
        # Recorded first: the receipt is sealed on the thread's own end, which
        # arrives after this, and it needs this result in it.
        self._record_receipt_result(result)
        self._restore_buttons()

        if not self._cancel_requested:
            if result.success:
                self.progress_widget.show_completion(
                    tr_format(self.tr("Complete — %1 cards created"), result.cards_created)
                )
            else:
                self.progress_widget.reset()
                self.progress_widget.set_status(self.tr("Failed — see log"))

        if result.success:
            # The curation snapshots are also the completed run's immutable
            # inputs; live selectors may already hold the next pair.
            video_file = self._curation_video
            subtitle_file = self._curation_subtitle
            completed_offset = self._curation_offset
            if video_file is not None and subtitle_file is not None:
                self.recent_manager.add_entry(video_file, subtitle_file, completed_offset)
                self._refresh_recent_combo()

            # Clear only inputs still owned by this run. A pair selected while
            # processing stays ready for the next run.
            if self._curation_video_raw is not None and self.video_selector.path_or_none() == self._curation_video_raw:
                self.video_selector.clear()
            if (
                self._curation_subtitle_raw is not None
                and self.subtitle_selector.path_or_none() == self._curation_subtitle_raw
            ):
                self.subtitle_selector.clear()

        # Show result
        self.presenter.show_processing_result(result)

        if result.success:
            # Reset per-run override so next Process uses Auto unless user picks again.
            # Failed runs keep the override intact so the user can retry with the same
            # track pick without having to reopen the Tracks dialog.
            self._audio_track_override = None

    def _on_processing_error(self, error_message: str) -> None:
        """Handle processing error signal.

        Args:
            error_message: Error message
        """
        self._mark_receipt_failed()
        self._restore_buttons()

        # Show error
        self.presenter.show_error(error_message)

        # Reset progress
        self.progress_widget.reset()
        self.progress_widget.set_status(self.tr("Failed — see log"))

        # Keep the audio-track override on the error path so the user can retry
        # without having to reopen the Tracks dialog (consistent with failed results).

    def _refresh_recent_combo(self) -> None:
        """Refresh the recent files combo box from disk."""
        self.recent_combo.blockSignals(True)
        self.recent_combo.clear()
        self.recent_combo.addItem(self.tr("Select recent file pair..."))

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
        # The offset spinbox is a per-session value the user dials in for the
        # current episode; it is never persisted back to config. Only follow
        # config.subtitle_offset when the *persisted* value actually changed,
        # so an unrelated settings save / theme toggle (each of which re-fires
        # update_config) doesn't wipe the in-progress offset back to 0.0.
        if config.subtitle_offset != self.config.subtitle_offset:
            self.offset_spinbox.setValue(config.subtitle_offset)
        self.config = config

    def shutdown(self) -> None:
        """Invalidate in-flight Tracks/Timing probe callbacks before app close.

        ``MiningTabBase.shutdown`` (called by ``BackgroundTaskController.shutdown``
        for every mining tab) cancels the curation dialog and joins leaked runs;
        bumping the generation first marks any Tracks/Timing probe callback
        already queued for delivery as stale, so it never touches a button that
        close may be tearing down (M7).
        """
        # getattr: test doubles that subclass this tab and skip its __init__
        # (e.g. duck-typed shutdown-call tests) never set the attribute.
        self._teardown_generation = getattr(self, "_teardown_generation", 0) + 1
        super().shutdown()

    def release_dictionary_resources(self) -> bool:
        """Close sqlite handles cached by the most recent worker run.

        The processor is created fresh per run, but the finished worker
        retains it (exposed via ``curation_processor``) until a new run
        replaces ``self.worker_thread``. On Windows those cached handles
        keep ``index.sqlite`` locked, so Settings → Remove / Re-import fails
        after the user has mined at least once (Issue #30 follow-up).

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

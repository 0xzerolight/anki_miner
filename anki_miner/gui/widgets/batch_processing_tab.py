"""Enhanced batch processing tab with modern UI design."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QKeySequence
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
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils import queue_state_store, result_copy
from anki_miner.gui.utils.keyboard_shortcuts import scoped_shortcut
from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    configure_card_layout,
    field_label_width,
    make_label_fit_text,
    page_filler,
)
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

    #: Tables and queue rows genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the pinned
    #: bar gets a stage and a progress bar (D17, D22). One id for both paths --
    #: the screen runs either the folder pairs or the series queue, never both.
    TASK_ID = "run.batch"
    TASK_OWNER = CapabilityTarget("video", "batch")

    #: Stable filename for this queue's recovery snapshot (D16-C).
    QUEUE_STATE_KEY = "queue.batch"

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
        self._run_had_item_failures = False
        # Both start methods assign the same union-typed worker_thread, so the
        # active path (Queue = two-level series items vs Quick = one episode
        # per item) is tracked explicitly for _on_progress_update's branch.
        self._queue_mode = False
        self._items_done = 0
        self._items_total = 0
        self._run_terminal_ids: set[str] = set()
        self._current_item_label = ""
        # The exact series the next queue run will mine, snapshotted from the
        # panel when Process Queue is pressed.
        self._run_selection: list = []

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

        # Create container widget for scroll area
        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Quick Processing Section
        quick_section = self._create_quick_processing_section()
        layout.addWidget(quick_section)

        # Multi-Series Queue Panel (extracted component). It binds its rows to
        # THIS queue, so removal, reorder and edits mutate one model rather than
        # the panel keeping a second, divergent copy (D28).
        self.queue_panel = QueuePanel(queue=self.batch_queue)
        self.queue_panel.process_requested.connect(self._process_queue)
        self.queue_panel.queue_controls.pause_requested.connect(self._on_pause_requested)
        self.queue_panel.queue_controls.resume_requested.connect(self._on_resume_requested)
        self.queue_panel.queue_controls.finish_current_requested.connect(self._on_finish_current_requested)
        self.queue_panel.empty_changed.connect(self._on_queue_empty_changed)
        # No stretch factor: the panel's own list makes it expand while there is
        # something to show. A stretch would keep the panel expanding even after
        # an empty queue hides that list, and the page could never hand the
        # height back.
        layout.addWidget(self.queue_panel)

        # Issue #60: opt-in word curation popup (default off). Season-level on
        # this tab: one popup per series (queue) / per run (quick pairs).
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self.review_words_checkbox.setChecked(False)
        self.review_words_checkbox.setToolTip(
            self.tr("Show the word-selection popup once per series, covering every episode's words")
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
        # The durable end state of this same card (D20). The noun is set per
        # run: the quick path mines episodes, the queue path whole series.
        self._install_receipt(layout, self.overall_progress_widget)

        # Retry Failed button (hidden by default)
        self.retry_button = ModernButton(self.tr("Retry Failed"), variant="secondary")
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self._retry_failed_items)
        layout.addWidget(self.retry_button)

        # Log widget; install_workflow_shell moves it into the Activity drawer (D6).
        self.log_widget = LogWidget()

        # Stands in for the queue list while an empty queue keeps it hidden, so
        # the page's leftover height still pools below the cards instead of
        # inflating their headings.
        self.page_filler = page_filler()
        layout.addWidget(self.page_filler)

        # Connect presenter signals to log widget
        self.presenter.info_signal.connect(self.log_widget.append_info)
        self.presenter.success_signal.connect(self.log_widget.append_success)
        self.presenter.warning_signal.connect(self.log_widget.append_warning)
        self.presenter.error_signal.connect(self.log_widget.append_error)

        container.setLayout(layout)

        # Scroll, Activity drawer, pinned bar (D6). Process Queue is the run
        # this screen is for, so it is the pinned action; Process Folder stays
        # in the quick-processing card with the folders it reads.
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._install_action_bar(
            main_layout,
            scroll_area,
            container,
            self.PAGE_WIDTH,
            primary=self.queue_panel.process_queue_button,
            secondary=(self.cancel_button,),
            log=self.log_widget,
        )
        self.setLayout(main_layout)
        self.install_issue_banner(main_layout)

        # Set up keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """Set up tab-specific keyboard shortcuts.

        Ctrl+Enter is installed by ``_install_action_bar``, which routes it
        through the queue's own Process Queue button; the copy that used to live
        here called ``_process_queue`` directly, so it ignored whether the button
        was enabled and could start a second run over a first.
        """
        # Ctrl+O: browse for the folder. Scoped, because Single owns Ctrl+O too
        # and both pages live in one window -- unscoped, the hidden one could win.
        scoped_shortcut(
            self,
            QKeySequence("Ctrl+O"),
            lambda: (self.video_folder_selector.browse() if hasattr(self, "video_folder_selector") else None),
        )

        # Ctrl+Shift+A: Add series to queue
        scoped_shortcut(self, QKeySequence("Ctrl+Shift+A"), self.queue_panel.add_series_external)

    def _create_quick_processing_section(self) -> QFrame:
        """Create the quick processing section with card styling.

        Returns:
            Frame with quick processing controls
        """
        section = QFrame()
        section.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        # Section header
        header = SectionHeader(title=self.tr("Quick Processing"))
        layout.addWidget(header)

        # Shared label-column width so both folder rows and the offset row line up.
        # Measure the TRANSLATED strings (see single_episode_tab): sizing on the
        # English literals clips every non-English locale.
        label_w = field_label_width(
            self.tr("Video Folder:"),
            self.tr("Subtitle Folder:"),
            self.tr("Subtitle Offset:"),
        )

        # Video folder selector
        self.video_folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
            file_filter="",
            label_width=label_w,
            history_key="video.batch.inputs",
        )
        layout.addWidget(self.video_folder_selector)

        # Subtitle folder selector
        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
            file_filter="",
            label_width=label_w,
            history_key="video.batch.inputs",
        )
        layout.addWidget(self.subtitle_folder_selector)

        # Constant subtitle offset applied to every episode pair in the folder
        # (mirrors the Single Episode tab; per-session, seeded from config).
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

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setToolTip(self.tr("Cancel processing"))
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.hide()
        button_layout.addWidget(self.cancel_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        section.setLayout(layout)

        # No explicit minimum height: an explicit minimum OVERRIDES the (larger)
        # layout-derived one (qSmartMinSize), letting the scroll area compress the
        # card below its content and clip the selectors' status captions.
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

        self.clear_screen_issue()

        folders = self._get_validated_folders()
        if not folders:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose existing video and subtitle folders.")))
            return

        video_folder, subtitle_folder = folders
        pairs = self._find_episode_pairs(video_folder, subtitle_folder)

        if not pairs:
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("No subtitle file could be matched to any video file in those folders."))
            )
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
        self._begin_receipt(len(pairs), item_noun=self.tr("episodes"))

        # Hide action buttons, show cancel
        self._is_processing = True
        self._show_cancel_state()

        # Log start
        self.presenter.show_info(tr_format(self.tr("Starting batch processing of %1 episodes..."), len(pairs)))
        self._publish_task_start(self.tr("Batch mining"), total=len(pairs))

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
        # Sealed on the thread's own end: the terminal summary the worker emits
        # on every exit path -- including a cancellation -- has arrived by then.
        self.worker_thread.finished.connect(self._on_run_thread_finished)
        self.worker_thread.start()

    def _warn_incomplete_items(self) -> None:
        """Show warnings for incomplete queue items."""
        incomplete = self.queue_panel.get_incomplete_items()
        for widget, issue_type in incomplete:
            if issue_type == "invalid":
                self.show_screen_issue(
                    ScreenIssue(
                        summary=tr_format(self.tr("%1 was skipped: its folders no longer exist."), widget.display_name)
                    )
                )
            else:
                self.show_screen_issue(
                    ScreenIssue(
                        summary=tr_format(self.tr("%1 was skipped: it is missing a folder."), widget.display_name)
                    )
                )

    def _start_queue_worker(self) -> None:
        """Create and start the queue worker thread."""
        from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread

        # Tear down any prior run before building the queue worker (Windows
        # back-to-back-mining freeze: leaked sqlite/Session handles).
        self._teardown_previous_run("batch")

        # The panel's snapshot when Process Queue supplied one; otherwise every
        # pending row, which is what Retry Failed hands over after resetting
        # them. Either way the worker is told exactly what it will mine.
        items = self._run_selection or [
            item for item in self.batch_queue.get_all_items() if item.status == QueueItemStatus.PENDING
        ]
        self._run_selection = []

        # Whole series per item on this path, so the receipt counts series.
        self._begin_receipt(len(items), item_noun=self.tr("series"))
        self._publish_task_start(self.tr("Batch mining"), total=len(items))

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        # Construct, connect and start under one rollback: the queue is locked
        # by now (D29-A), and a failure anywhere in here would otherwise leave it
        # frozen against a run that never began, with no thread whose `finished`
        # could ever unfreeze it.
        try:
            worker = BatchQueueWorkerThread(
                self.batch_queue,
                self.config,
                self.presenter,
                self.progress_callback,
                stats_service=self.stats_service,
                curation_callback=curation_cb,
                items=items,
            )
            self.worker_thread = worker

            worker.queue_started.connect(self._on_queue_started)
            worker.item_started.connect(self._on_item_started)
            worker.item_pairs_progress.connect(self._on_item_pairs_progress)
            worker.item_completed.connect(self._on_item_completed)
            worker.item_failed.connect(self._on_item_failed)
            worker.queue_finished.connect(self._on_queue_finished)
            worker.run_paused.connect(self._on_run_paused)
            worker.run_resumed.connect(self._on_run_resumed)
            # Run-level fatals (stale-dict gate, processor-build failure) emit
            # error THEN queue_finished — without the flag the terminal handler
            # would read "Complete — 0 cards created" on a failed run.
            worker.error.connect(self._on_queue_worker_error)
            # Safety net (G1): restore the action buttons once the thread ends.
            # The quick (manual-pair) path already wires this; without it a
            # caught run-level failure (stale-dict gate, AnkiService
            # construction) leaves the buttons stranded in the running state.
            worker.finished.connect(self._restore_buttons)
            worker.finished.connect(self._on_run_thread_finished)

            worker.start()
        except Exception as exc:  # noqa: BLE001 - the run never began; surface and recover
            logger.exception("BatchProcessingTab failed to start the queue worker")
            self.worker_thread = None
            self._run_failed = True
            self._on_queue_worker_error(str(exc))
            self._restore_buttons()
            self._on_run_thread_finished()

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
        season_map = getattr(w, "_curation_media_map", None)
        if media_context is not None and season_map:
            # Season curation: give the dialog a resolver over a SNAPSHOT of
            # the worker's episode map, so cross-episode word focus can rebuild
            # the player context without ever touching the worker after it
            # unparks. _make_curation_media_context is static, pure and
            # error-swallowing, so the resolver is safe off the GUI thread.
            snapshot = dict(season_map)
            config = self.config

            def _resolve(video: Path) -> CurationMediaContext | None:
                entry = snapshot.get(video)
                if entry is None:
                    return None
                subtitle, offset = entry
                return MiningTabBase._make_curation_media_context(config, video, subtitle, offset=offset)

            media_context = replace(media_context, context_resolver=_resolve)
        return media_context, self._lookup_fn_from_processor(w.curation_processor)

    def _empty_run_summary(self) -> str:
        """Why a Process Queue click found nothing to mine."""
        if self.queue_panel.has_only_completed_rows():
            return self.tr(
                "Every series in the queue is already complete. "
                "Select the ones you want to mine again, then click Run selected."
            )
        return self.tr("No valid series in the queue to process.")

    def _process_queue(self) -> None:
        """Process all items in queue."""
        if self._is_processing:
            return

        # A fresh attempt supersedes the complaint about the last one: the user
        # who filled the queue this banner objected to must not still be reading
        # "No valid series" over a run that is now working. After the reentrancy
        # guard, so a click landing on a live run cannot wipe that run's problem,
        # and before the checks below, which re-raise whatever is still wrong.
        self.clear_screen_issue()

        # The rows themselves are the model now: each one bound to a persistent
        # QueueItem when its folders validated. The queue is NOT rebuilt here --
        # doing so would mint new identities and lose the episode receipts that
        # stop a retry re-mining pairs already in Anki (D28, D30).
        self._run_selection = self.queue_panel.runnable_items()

        if not self._run_selection:
            self.show_screen_issue(ScreenIssue(summary=self._empty_run_summary()))
            return

        self._warn_incomplete_items()

        # Prepare UI for processing
        self._is_processing = True
        self.log_widget.clear_log()
        self._begin_run(queue_mode=True)
        self._show_cancel_state()
        self.presenter.show_info(
            tr_format(self.tr("Starting queue processing (%1 series)..."), len(self._run_selection))
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
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.queue_panel.set_buttons_enabled(False)
        # D29-A: the list is frozen for the duration, so the progress numbers,
        # the lock state and the receipt all describe the same set of series.
        self.queue_panel.set_locked(True)

    def _restore_buttons(self) -> None:
        """Restore normal button state after processing ends."""
        self._is_processing = False
        self.cancel_button.hide()
        self.process_pairs_button.show()
        self._set_buttons_enabled(True)
        self.queue_panel.set_locked(False)
        # Cancel recovery: the Quick-path worker suppresses result_ready on a
        # cancelled run, so QThread.finished (always fires) is the only safe
        # place to replace "Cancelling…". Idempotent for the queue path,
        # whose _on_queue_finished also handles the flag. The bar is left where
        # it froze: how many episodes were actually done is the point.
        if self._cancel_requested:
            self.overall_progress_widget.set_status(self.tr("Cancelled"))

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._on_cancel_clicked()

    def _on_cancel_clicked(self) -> None:
        """Cancel the run: one verb, no prompt, and no invented progress after it."""
        self._cancel_requested = True
        self._publish_task_cancelling()
        # Release any open curation dialog first so the worker doesn't hang (Issue #60).
        self._cancel_active_curation_dialog()
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling…"))
        self.cancel_button.setEnabled(False)
        self.overall_progress_widget.freeze()
        self.overall_progress_widget.set_status(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Boundary controls (D29-A)
    # ------------------------------------------------------------------

    def _boundary_worker(self) -> BatchQueueWorkerThread | None:
        """The active queue worker, or None on the folder-pairs path.

        Only the series queue has boundaries: a Quick run is one list of
        episodes inside one worker, with nothing to pause between.
        """
        worker = self.worker_thread
        from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread as _QueueWorker

        return worker if isinstance(worker, _QueueWorker) else None

    def _on_queue_empty_changed(self, is_empty: bool) -> None:
        """Show the filler exactly while the panel has hidden its list.

        The panel's list is what makes the panel take this page's leftover
        height. With the list gone that height has to land somewhere, and the
        headings are the wrong somewhere.
        """
        self.page_filler.setVisible(is_empty)

    def _on_pause_requested(self) -> None:
        """Ask the run to stop at the next series boundary."""
        worker = self._boundary_worker()
        if worker is None:
            return
        worker.request_pause_after_current()
        self.queue_panel.queue_controls.pause_button.setEnabled(False)

    def _on_resume_requested(self) -> None:
        """Let a paused run carry on."""
        worker = self._boundary_worker()
        if worker is not None:
            worker.resume()

    def _on_finish_current_requested(self) -> None:
        """Let the series being mined finish, then end the run.

        Distinct from Cancel, which abandons the series in flight. Neither asks
        for confirmation (D22, D24).
        """
        worker = self._boundary_worker()
        if worker is None:
            return
        worker.request_stop_after_current()
        self.queue_panel.queue_controls.finish_button.setEnabled(False)
        self.queue_panel.queue_controls.pause_button.setEnabled(False)

    def _on_run_paused(self) -> None:
        """Report where the run stopped, and offer to continue from there."""
        self.queue_panel.queue_controls.set_paused(True, done=self._items_done, total=self._items_total)

    def _on_run_resumed(self) -> None:
        """Return the badge and the button to their running state."""
        self.queue_panel.queue_controls.set_paused(False)

    def _begin_run(self, queue_mode: bool) -> None:
        """Reset the bar, flags, and per-run counters at run start."""
        self.overall_progress_widget.reset()
        self._cancel_requested = False
        self._run_failed = False
        self._run_had_item_failures = False
        self._queue_mode = queue_mode
        self._items_done = 0
        self._items_total = 0
        self._run_terminal_ids = set()
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
        self._publish_task_count(current=completed, total=total or None, detail="")

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
            cards_created: Number of cards created during this run
        """
        self._record_receipt_counts(notes_added=cards_created, failed=False)
        self._advance_queue_bar(item_id)
        self.presenter.show_success(result_copy.created_cards(cards_created, self.config.anki_deck_name))

        # Update queue panel — address the completed row by id (T-30).
        cumulative_cards = cards_created
        for item in self.batch_queue.get_all_items():
            if item.id == item_id:
                cumulative_cards = max(cumulative_cards, item.cards_created)
                break
        self.queue_panel.set_processing_item_complete(item_id, cumulative_cards)

    def _on_item_failed(self, item_id: str, error_message: str, cards_created: int = 0) -> None:
        """Called when an item fails.

        Render-only: the worker already set ERROR status and error_message
        before emitting (see BatchQueueWorkerThread.run).

        Args:
            item_id: Item ID
            error_message: Error message
            cards_created: Notes confirmed earlier in this series during this run
        """
        self._run_had_item_failures = True
        self._record_receipt_counts(notes_added=cards_created, failed=True)
        self.presenter.show_error(error_message)
        self._advance_queue_bar(item_id)

        # Render the failed row with the error badge — the worker set the model
        # QueueItem's status but never drove the widget, so the row otherwise
        # stuck at "Processing" during the run and fell back to "Pending" after.
        self.queue_panel.set_item_status(item_id, "error")

    def _on_item_pairs_progress(self, item_id: str, done: int, total: int) -> None:
        """Fill the bar within a series from the worker's real episode counts.

        The composed value is ``(series_done + done/total) / series_total`` —
        every quantity a count the worker actually has (``total`` is this run's
        pending-pair list for the series), so this honours the no-fabricated-
        fill rule that keeps stage weights off the bar. Monotone against the
        boundary ticks: the final ``(n, n)`` emit equals the percent
        ``_advance_queue_bar`` recomputes, and the next series' ``(0, m)``
        emit reproduces it again.

        Args:
            item_id: The series being mined (unused; the bar is run-scoped)
            done: Pairs whose attempt concluded so far in this series
            total: Pending pairs in this series for this run; ``<= 0`` no-ops
        """
        if self._items_total <= 0 or total <= 0:
            return
        fraction = min(done / total, 1.0)
        percent = int((self._items_done + fraction) / self._items_total * 100)
        self.overall_progress_widget.set_percent(percent)

    def _advance_queue_bar(self, item_id: str) -> None:
        """Advance the series-granular bar after a terminal item outcome.

        The queue path is TWO-LEVEL (one item = a series of N episodes whose
        count is unknown up front), so the bar moves per whole series here;
        between these boundary ticks ``_on_item_pairs_progress`` fills with the
        series' real episode counts, and the stage detail stays in words.

        Counted over a RUN-LOCAL set of item ids rather than the queue's
        all-time ``completed_count + failed_count``: retrying 2 failures after 8
        earlier successes made that sum read 9 then 10 against a run total of 2
        — a bar claiming "10/2" and pinned at 100% from the first item.
        """
        self._run_terminal_ids.add(item_id)
        self._items_done = len(self._run_terminal_ids)
        self.overall_progress_widget.set_composed(self._items_done, 0, self._items_total)
        self._publish_task_count(current=self._items_done, total=self._items_total or None, detail="")

    def _on_queue_finished(self, total_cards: int) -> None:
        """Called when entire queue finishes.

        The run's summary is no longer a modal box raised from here. It is the
        inline receipt, sealed when the worker thread ends — the box fired on
        the cancel path too, congratulating the user on a run they had just
        stopped (D20).

        Args:
            total_cards: Total cards created during this run
        """
        self._restore_buttons()

        # Terminal end state: cancel -> failed -> success. A cancelled run keeps
        # its frozen bar; only a fatal failure clears it.
        if self._cancel_requested:
            self.overall_progress_widget.set_status(self.tr("Cancelled"))
        elif self._run_failed:
            self.overall_progress_widget.reset()
            self.overall_progress_widget.set_status(self.tr("Failed — see log"))
        elif self._run_had_item_failures:
            self.overall_progress_widget.reset()
            self.overall_progress_widget.set_status(self.tr("Finished with errors — see log"))
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

    # ------------------------------------------------------------------
    # Durable queue contents (D16-C)
    # ------------------------------------------------------------------

    def queue_snapshot(self) -> QueueSnapshot:
        """Describe the series queue as folder pairs and outcomes.

        ``committed_pair_keys`` is deliberately absent. It is live run
        provenance owned by W5-T5, and a restored row is an unknown that never
        retries automatically — persisting a second, weaker copy of the write
        journal would only create somewhere for the two to disagree.
        """
        return QueueSnapshot(
            key=self.QUEUE_STATE_KEY,
            items=tuple(
                QueueItemSnapshot(
                    item_id=item.id,
                    source=queue_state_store.folder_pair_source(
                        item.video_folder, item.subtitle_folder, offset=item.subtitle_offset
                    ),
                    title=item.display_name,
                    status=queue_state_store.status_from_run_state(item.status.value),
                    retry_count=item.retry_count,
                    error=item.error_message or "",
                    result_count=item.cards_created,
                )
                for item in self.batch_queue.get_all_items()
            ),
        )

    def restore_queue_snapshot(self, snapshot: QueueSnapshot) -> int:
        """Rebuild the series queue from ``snapshot``; return the row count.

        A row that was mid-run comes back as an error saying so, which is what
        keeps it out of the next Process Queue: only the user pressing Retry
        turns it back into a pending row.
        """
        if self._is_processing or self.batch_queue.get_all_items():
            return 0
        restored = 0
        for row in snapshot.items:
            source = row.source
            video = Path(str(source["video"]))
            subtitle = Path(str(source["subtitle"]))
            status = QueueItemStatus.PENDING.value
            error = ""
            missing = row.missing_paths()
            if missing:
                status = QueueItemStatus.ERROR.value
                error = tr_format(self.tr("Folder not found: %1"), str(missing[0]))
            elif row.is_interrupted:
                status = QueueItemStatus.ERROR.value
                error = self.tr("Interrupted when Anki Miner closed")
            elif row.status == queue_state_store.STATUS_COMPLETED:
                status = QueueItemStatus.COMPLETED.value
            elif row.status == queue_state_store.STATUS_ERROR:
                status = QueueItemStatus.ERROR.value
                error = row.error
            self.queue_panel.restore_item(
                item_id=row.item_id,
                display_name=row.title,
                video_folder=video,
                subtitle_folder=subtitle,
                subtitle_offset=float(source.get("offset", 0.0) or 0.0),
                status=status,
                cards_created=row.result_count,
                retry_count=row.retry_count,
                error_message=error,
            )
            restored += 1
        self.queue_panel.update_stats()
        return restored

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

        An empty ``item_description`` shows the prefix alone — never a dangling
        "name — ".
        """
        if item_description and self._current_item_label:
            return f"{self._current_item_label} — {item_description}"
        if item_description:
            return item_description
        return self._current_item_label or None

    def _set_progress_status(self, label: str) -> None:
        """Write the stage line onto the Overall bar, behind the item prefix."""
        status = self._compose_status(label)
        if status:
            self.overall_progress_widget.set_status(status)

    def _on_progress_stage(self, index: int, total: int, name: str) -> None:
        """Per-episode stage: status only.

        Unlike a single-episode run, the bar here counts episodes/series, so a
        stage inside one of them must not move it — that is exactly the blend
        that made a long episode look like a stalled batch.
        """
        self._stage_line.on_stage(index, total, name)
        self._publish_task_stage(index, total, name)

    def _on_progress_start(self, total: int, description: str) -> None:
        """Per-episode stage start: status only (the bar counts whole items).

        Args:
            total: Items in this stage (used for the true count in the label)
            description: Stage description
        """
        self._stage_line.on_start(total, description)

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Per-episode within-stage progress: status label only.

        Args:
            current: True item number inside the current stage
            item_description: Stage/item detail
        """
        self._stage_line.on_progress(current, item_description)

    def _on_progress_complete(self) -> None:
        """Per-episode stage complete: no-op (terminal handlers own the summary)."""

    def _on_processing_finished(self, results: list) -> None:
        """Record the manual-pair run's results and paint its terminal bar.

        The worker emits this on every exit path, cancellation included, so the
        receipt sealed afterwards on ``QThread.finished`` can state what a
        stopped run managed to do. Failed episodes come back as results with
        ``errors`` populated (``process_episode`` never raises), so they are
        classified rather than counted as successes (Issue #51).

        Args:
            results: List of processing results
        """
        for result in results:
            self._record_receipt_result(result)
        self._run_had_item_failures = any(not result.success for result in results)
        self._restore_buttons()

        # A cancelled run keeps its frozen bar and says nothing here: the
        # receipt sealed on QThread.finished states what the stopped run
        # managed to do, and a modal announcing "Complete" is the last thing
        # someone who just pressed Cancel wants (D20/D22).
        if self._cancel_requested:
            return
        if self._run_had_item_failures:
            self.overall_progress_widget.reset()
            self.overall_progress_widget.set_status(self.tr("Finished with errors — see log"))
        else:
            self.overall_progress_widget.show_completion(
                tr_format(self.tr("Complete — %1 cards created"), sum(r.cards_created for r in results))
            )

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

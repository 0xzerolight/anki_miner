"""Deck Builder tab: mine one show's season into a single named Anki deck.

Built on Batch's season pipeline: :class:`DeckBuilderWorker` (a
``BatchQueueWorkerThread`` subclass) mines the folder pair as one season item
under a build-only config, gated on a corpus preview before the actual build
runs. This module owns the screen's inputs, the request they build, and the
run's lifecycle: Preview scans and waits at the gate, Build confirms (or
starts a run already confirmed), Cancel stops at any stage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import queue_state_store
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets._folder_series_screen import FolderSeriesScreenBase
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    cap_row_field,
    configure_card_layout,
    field_label_width,
    make_label_fit_text,
)
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker
from anki_miner.models.deck_build import DeckBuildRequest, DeckCorpus, DeckSelectionMode
from anki_miner.services.corpus_aggregator import build_preview, rank_select
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

#: Shared by every folder picker on this screen (D7): Browse reopens where the
#: last one of these three left off, independent of Batch's own history.
_HISTORY_KEY = "video.deckbuilder.inputs"


class DeckBuilderTab(FolderSeriesScreenBase):
    """Mine one show's video/subtitle folders into a single named Anki deck.

    Subclasses :class:`FolderSeriesScreenBase` for the same folder-pair inputs
    Batch's Add Series card uses (video, subtitle, optional translation
    folder, offsets), then adds the deck name and word-selection controls a
    season build needs. The corpus preview and the actual build both run on
    one :class:`DeckBuilderWorker`; this class owns the inputs, their
    validation, the request they build, and the run's four states (see
    :meth:`_apply_run_state`).
    """

    #: Tables of results and log lines genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the pinned
    #: bar gets a stage and a progress bar (D17, D22), mirroring Batch.
    TASK_ID = "run.deckbuilder"
    TASK_OWNER = CapabilityTarget("video", "deckbuilder")

    #: Published so this screen's build survives a crash mid-run (D16-C): a
    #: confirmed build may already have written cards to Anki, which is what
    #: makes it worth restoring the form for -- see :meth:`queue_snapshot`.
    QUEUE_STATE_KEY = "queue.deckbuilder"

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback,
        stats_service=None,
        parent=None,
    ):
        """Initialize the Deck Builder tab.

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
        self.worker_thread: DeckBuilderWorker | None = None
        # Tracks the last value auto-filled from the video folder name, so a
        # manual edit (text no longer equal to it) is never overwritten again.
        self._last_auto_deck_name: str = ""
        # Run state, reset at every start. Set before _setup_ui: seeding the
        # selection controls already fires _refresh_preview.
        self._run_state = "idle"
        self._corpus: DeckCorpus | None = None
        self._confirmed_selection: tuple[DeckSelectionMode, float] | None = None
        self._cancel_requested = False
        self._run_failed = False
        self._run_had_item_failures = False

        self._wire_progress_callback(self.progress_callback)
        self._init_curation_bridge()
        self._setup_drag_drop()

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        scroll_area = QScrollArea()

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_input_section())
        layout.addWidget(self._create_settings_section())
        layout.addWidget(self._create_results_section())

        layout.addWidget(SectionHeader(self.tr("Progress")))
        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)
        # The durable end state of this same run (D20); one item per run, so
        # the noun is only ever used above one deck.
        self._install_receipt(layout, self.progress_widget, item_noun=self.tr("deck"))

        # Carries its own header and styling; install_workflow_shell moves it into the Activity drawer (D6).
        self.log_widget = LogWidget(source=self.TASK_ID or type(self).__name__)

        # Connect presenter signals to log widget (mirrors Batch/Single).
        self.presenter.info_signal.connect(self.log_widget.append_info)
        self.presenter.success_signal.connect(self.log_widget.append_success)
        self.presenter.warning_signal.connect(self.log_widget.append_warning)
        self.presenter.error_signal.connect(self.log_widget.append_error)

        container.setLayout(layout)

        # Action buttons. Build is the run this screen is for, so it is the
        # pinned primary; Preview and Cancel are the quieter actions beside
        # it. Their enabled states come from _apply_run_state alone.
        self.preview_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_button.setToolTip(self.tr("Scan the season and preview which words will be included"))
        self.preview_button.clicked.connect(self._on_preview_clicked)

        self.build_button = ModernButton(self.tr("Build Deck"), variant="primary")
        self.build_button.setToolTip(
            self.tr(
                "Create the Anki cards for the selected words, scanning the season first if you have not previewed it"
            )
        )
        self.build_button.clicked.connect(self._on_build_clicked)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.install_issue_banner(main_layout)
        self._install_action_bar(
            main_layout,
            scroll_area,
            container,
            self.PAGE_WIDTH,
            primary=self.build_button,
            secondary=(self.preview_button, self.cancel_button),
            log=self.log_widget,
        )
        self.setLayout(main_layout)

        self._apply_secondary_gate()
        self._seed_selection_controls()
        self._apply_run_state("idle")

    def _create_input_section(self) -> QFrame:
        """Build the Input card: the season's folder pair, plus their offsets.

        Mirrors Batch's Add Series card (video, subtitle, optional
        translation folder, offsets) -- this screen mines the whole pair as
        one season directly, rather than adding it to a queue first.
        """
        section = QFrame()
        section.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Input")))

        # Measure the TRANSLATED strings (see single_episode_tab): sizing on
        # the English literals clips every non-English locale.
        label_w = field_label_width(
            self.tr("Video Folder:"),
            self.tr("Subtitle Folder:"),
            self.tr("Subtitle Offset:"),
            self.tr("Translation Folder:"),
            self.tr("Translation Offset:"),
        )

        self.video_folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
            file_filter="",
            label_width=label_w,
            history_key=_HISTORY_KEY,
        )
        layout.addWidget(self.video_folder_selector)
        # Auto-fill deck name from folder name.
        self.video_folder_selector.path_changed.connect(self._on_video_folder_changed)
        self.video_folder_selector.path_changed.connect(self._reset_preview_if_idle)

        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
            file_filter="",
            label_width=label_w,
            history_key=_HISTORY_KEY,
        )
        layout.addWidget(self.subtitle_folder_selector)
        self.subtitle_folder_selector.path_changed.connect(self._reset_preview_if_idle)

        # Secondary-language subtitles, gated on the Settings toggle -- its own
        # folder, not a language suffix inside the subtitle folder, mirroring
        # Batch's F7 rationale (episode-number pairing consumes each subtitle
        # once).
        self.secondary_folder_selector = FileSelector(
            label=self.tr("Translation Folder:"),
            file_mode=False,
            file_filter="",
            label_width=label_w,
            history_key=_HISTORY_KEY,
        )
        layout.addWidget(self.secondary_folder_selector)

        # Constant subtitle offset applied to every episode in the season
        # (mirrors Batch's Add Series card; per-session, seeded from config).
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
            self.tr("Adjust subtitle timing for the whole season (positive = later, negative = earlier)")
        )

        offset_layout.addWidget(offset_label)
        offset_layout.addWidget(self.offset_spinbox)
        offset_layout.addStretch()
        layout.addLayout(offset_layout)

        # Wrapped in a QWidget so the gate can hide the whole row: a bare
        # QHBoxLayout has nothing to setVisible().
        self.secondary_offset_row = QWidget()
        secondary_offset_layout = QHBoxLayout(self.secondary_offset_row)
        secondary_offset_layout.setContentsMargins(0, 0, 0, 0)
        secondary_offset_layout.setSpacing(SPACING.xs)

        secondary_offset_label = QLabel(self.tr("Translation Offset:"))
        secondary_offset_label.setObjectName("field-label")
        secondary_offset_label.setMinimumWidth(label_w)
        make_label_fit_text(secondary_offset_label)

        self.secondary_offset_spinbox = QDoubleSpinBox()
        self.secondary_offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        self.secondary_offset_spinbox.setSingleStep(0.5)
        self.secondary_offset_spinbox.setValue(0.0)
        self.secondary_offset_spinbox.setSuffix(self.tr(" seconds"))
        self.secondary_offset_spinbox.setToolTip(
            self.tr("Shift the translation subtitles only (positive = later, negative = earlier)")
        )
        secondary_offset_label.setBuddy(self.secondary_offset_spinbox)

        secondary_offset_layout.addWidget(secondary_offset_label)
        secondary_offset_layout.addWidget(self.secondary_offset_spinbox)
        secondary_offset_layout.addStretch()
        layout.addWidget(self.secondary_offset_row)

        section.setLayout(layout)
        return section

    def _create_settings_section(self) -> QFrame:
        """Build the Deck Settings card: name, word selection, filters."""
        section = QFrame()
        section.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Deck Settings")))

        label_w = field_label_width(self.tr("Deck Name:"), self.tr("Word Selection:"))

        # Deck name row.
        deck_row = QHBoxLayout()
        deck_row.setSpacing(SPACING.xs)
        deck_label = QLabel(self.tr("Deck Name:"))
        deck_label.setObjectName("field-label")
        deck_label.setMinimumWidth(label_w)
        deck_row.addWidget(deck_label)
        self.deck_name_edit = QLineEdit()
        self.deck_name_edit.setPlaceholderText(self.tr("Enter deck name…"))
        deck_row.addWidget(self.deck_name_edit, 1)
        # Hand-built row: same cap the FileSelectors above get themselves, or
        # this one field runs the whole page column while they stop short.
        cap_row_field(self.deck_name_edit, label_w, deck_row.spacing())
        deck_row.addStretch()
        layout.addLayout(deck_row)

        # Mode row.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_label = QLabel(self.tr("Word Selection:"))
        mode_label.setObjectName("field-label")
        mode_label.setMinimumWidth(label_w)
        mode_row.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.tr("All vocabulary"), userData=DeckSelectionMode.ALL)
        self.mode_combo.addItem(self.tr("Top N words"), userData=DeckSelectionMode.TOP_N)
        self.mode_combo.addItem(self.tr("Target coverage %"), userData=DeckSelectionMode.COVERAGE_PCT)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Value inputs (one per relevant mode; only one visible at a time).
        value_row = QHBoxLayout()
        value_row.setSpacing(SPACING.xs)
        value_row.addSpacing(label_w + SPACING.xs)  # align under the combo

        self.top_n_spinbox = QSpinBox()
        self.top_n_spinbox.setRange(1, 100_000)
        # Value seeded from config in _seed_selection_controls.
        self.top_n_spinbox.setSuffix(self.tr(" words"))
        self.top_n_spinbox.setToolTip(self.tr("Include the N most-frequent lemmas"))
        value_row.addWidget(self.top_n_spinbox)

        self.coverage_spinbox = QDoubleSpinBox()
        self.coverage_spinbox.setRange(1.0, 100.0)
        # Value seeded from config in _seed_selection_controls.
        self.coverage_spinbox.setDecimals(1)
        self.coverage_spinbox.setSuffix(" %")
        self.coverage_spinbox.setToolTip(self.tr("Include enough words to cover this percentage of tokens"))
        value_row.addWidget(self.coverage_spinbox)
        self.coverage_spinbox.hide()

        value_row.addStretch()
        layout.addLayout(value_row)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        # Collection filter.
        self.skip_known_checkbox = QCheckBox(self.tr("Skip words already in my Anki collection"))
        self.skip_known_checkbox.setToolTip(self.tr("Checked: skip your known words; unchecked: mine every word."))
        layout.addWidget(self.skip_known_checkbox)
        self.skip_known_checkbox.toggled.connect(
            lambda checked: self.persist_run_options(deck_builder_skip_known=checked)
        )
        self.skip_known_checkbox.toggled.connect(self._reset_preview_if_idle)

        # Issue #60: opt-in word curation popup (default off), shared across
        # all seven mining screens.
        self.review_words_checkbox = QCheckBox(self.tr("Review words before mining"))
        self._bind_review_words_checkbox()
        self.review_words_checkbox.setToolTip(self.tr("Pick which words get cards, once per series."))
        layout.addWidget(self.review_words_checkbox)

        self.top_n_spinbox.valueChanged.connect(lambda value: self.persist_run_options(deck_builder_top_n=value))
        self.coverage_spinbox.valueChanged.connect(
            lambda value: self.persist_run_options(deck_builder_coverage_pct=value)
        )

        # After a preview, the numbers follow the selection with no rescan.
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview)
        self.top_n_spinbox.valueChanged.connect(self._refresh_preview)
        self.coverage_spinbox.valueChanged.connect(self._refresh_preview)

        section.setLayout(layout)
        return section

    def _create_results_section(self) -> QFrame:
        """Build the Results card: the corpus-preview numbers ``_refresh_preview`` fills in."""
        section = QFrame()
        section.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Results")))

        # known_skipped counts selected lemmas that will not get a card this
        # run -- already known (skip_known on), or with no sentence a corpus
        # row ever carried them into. Neither cause is obvious from the number
        # alone, hence the tooltip.
        known_skipped_tooltip = self.tr(
            "Selected words that won't get a card this run: already in your collection, " "or with no sentence to mine."
        )

        self._result_labels: dict[str, QLabel] = {}
        for field_key, field_label, tooltip in (
            ("total_tokens", self.tr("Total tokens:"), ""),
            ("unique_lemmas", self.tr("Unique lemmas:"), ""),
            ("candidate_count", self.tr("Candidate words:"), ""),
            ("projected_coverage_pct", self.tr("Projected coverage:"), ""),
            ("known_skipped", self.tr("Already known (skipped):"), known_skipped_tooltip),
            ("card_count", self.tr("Cards to create:"), ""),
        ):
            row = QHBoxLayout()
            lbl = QLabel(field_label)
            lbl.setObjectName("field-label")
            lbl.setMinimumWidth(160)
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if tooltip:
                lbl.setToolTip(tooltip)
                val.setToolTip(tooltip)
            self._result_labels[field_key] = val
            row.addWidget(lbl)
            row.addWidget(val, 1)
            layout.addLayout(row)

        section.setLayout(layout)
        return section

    # ------------------------------------------------------------------
    # Slot: mode combo change
    # ------------------------------------------------------------------

    def _seed_selection_controls(self) -> None:
        """Seed the deck-settings controls from the remembered config.

        The visibility pass runs outside the guard: which value widget is
        shown follows the mode and is not persisted state of its own, so a
        restored Top N mode has to open with its spinbox already visible.
        """
        with self.seeding():
            index = self.mode_combo.findData(DeckSelectionMode(self.config.deck_builder_mode))
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
            self.top_n_spinbox.setValue(self.config.deck_builder_top_n)
            self.coverage_spinbox.setValue(self.config.deck_builder_coverage_pct)
            self.skip_known_checkbox.setChecked(self.config.deck_builder_skip_known)
        self._on_mode_changed(self.mode_combo.currentIndex())

    def _on_mode_changed(self, index: int) -> None:
        """Show/hide the value input appropriate for the selected mode."""
        mode = self.mode_combo.itemData(index)
        self.top_n_spinbox.setVisible(mode == DeckSelectionMode.TOP_N)
        self.coverage_spinbox.setVisible(mode == DeckSelectionMode.COVERAGE_PCT)
        # itemData is None for an out-of-range index, which a combo can report
        # while it is still being populated.
        if mode is not None:
            self.persist_run_options(deck_builder_mode=mode.value)

    # ------------------------------------------------------------------
    # Slot: video folder auto-fill
    # ------------------------------------------------------------------

    def _on_video_folder_changed(self, new_path: str) -> None:
        """Auto-fill deck name from the folder's basename.

        Only overwrites the deck-name field if it is currently empty or still
        contains the previous auto-filled value (i.e. the user has not edited
        it manually).
        """
        current_name = self.deck_name_edit.text().strip()
        if current_name == "" or current_name == self._last_auto_deck_name:
            auto_name = Path(new_path).name if new_path else ""
            self.deck_name_edit.setText(auto_name)
            self._last_auto_deck_name = auto_name

    def _reset_preview_if_idle(self, *_args: object) -> None:
        """Drop the cached preview when a scan input changes while idle.

        Otherwise the previous show's numbers stay on screen and a later
        mode/Top-N/coverage change (which recomputes from the cached corpus
        with no rescan) keeps describing that stale show. Only idle: these
        controls are locked for the rest of a run anyway (see
        :meth:`_apply_run_state`), so a live corpus never reaches here.
        """
        if self._run_state != "idle":
            return
        self._corpus = None
        for label in self._result_labels.values():
            label.setText("—")

    # ------------------------------------------------------------------
    # Drag-and-drop (locked outside idle)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept a drag only while the scan inputs are unlocked (idle).

        The base implementation routes straight to ``set_path`` on the
        selectors, bypassing their disabled state while a scan is pending or
        a build is running: a drop would then silently swap the show a
        pending Preview/Build is about to act on.
        """
        if self._run_state != "idle":
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Route a drop only while the scan inputs are unlocked (idle). See :meth:`dragEnterEvent`."""
        if self._run_state != "idle":
            return
        super().dropEvent(event)

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _get_validated_folders(self) -> tuple[Path, Path] | None:
        """The season's video/subtitle pair, or ``None`` when unset or missing on disk."""
        video_path = self.video_folder_selector.path_or_none()
        subtitle_path = self.subtitle_folder_selector.path_or_none()
        if video_path is None or subtitle_path is None:
            return None
        if not self.video_folder_selector.is_valid() or not self.subtitle_folder_selector.is_valid():
            return None
        return Path(video_path), Path(subtitle_path)

    def _build_request(self) -> DeckBuildRequest | None:
        """Validate every input and return the run request, or ``None`` on refusal.

        A refusal reports its reason through the screen issue banner and
        starts no worker; Preview and Build both read this and stop the same
        way. Whether the folders pair up any episodes is checked separately,
        in :meth:`_start`.
        """
        self.clear_screen_issue()

        folders = self._get_validated_folders()
        if folders is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose existing video and subtitle folders.")))
            return None
        video_folder, subtitle_folder = folders

        ok, secondary_folder = self._validated_secondary_folder(subtitle_folder)
        if not ok:
            return None

        deck_name = self.deck_name_edit.text().strip()
        if not deck_name:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Enter a deck name before mining.")))
            return None

        return DeckBuildRequest(
            video_folder=video_folder,
            subtitle_folder=subtitle_folder,
            deck_name=deck_name,
            skip_known=self.skip_known_checkbox.isChecked(),
            review=self.review_words_checkbox.isChecked(),
            subtitle_offset=self.offset_spinbox.value(),
            secondary_folder=secondary_folder,
            secondary_offset=self._secondary_offset(),
        )

    # ------------------------------------------------------------------
    # Run state
    # ------------------------------------------------------------------

    def _apply_run_state(self, state: str) -> None:
        """Set every run-dependent enable from one table.

        ``idle``: Preview, Build and every input on; Cancel off.
        ``scanning`` / ``preview_ready``: Build (which pre-confirms or
        confirms), Cancel and the selection on; Preview and the scan inputs
        off. ``building``: only Cancel on.

        The scan inputs shaped the corpus a preview describes, so they lock
        for the whole run. The selection stays live until Build, because
        changing it only re-reads the cached corpus.
        """
        self._run_state = state
        idle = state == "idle"
        building = state == "building"
        self.preview_button.setEnabled(idle)
        self.build_button.setEnabled(not building)
        self.cancel_button.setEnabled(not idle)
        self.cancel_button.setText(self.tr("Cancel"))
        for control in (
            self.video_folder_selector,
            self.subtitle_folder_selector,
            self.secondary_folder_selector,
            self.offset_spinbox,
            self.secondary_offset_spinbox,
            self.deck_name_edit,
            self.skip_known_checkbox,
            self.review_words_checkbox,
        ):
            control.setEnabled(idle)
        for selection_control in (self.mode_combo, self.top_n_spinbox, self.coverage_spinbox):
            selection_control.setEnabled(not building)

    def _current_selection(self) -> tuple[DeckSelectionMode, float]:
        """The word selection the controls show now, as ``(mode, value)``."""
        mode: DeckSelectionMode = self.mode_combo.currentData()
        if mode is DeckSelectionMode.TOP_N:
            return mode, float(self.top_n_spinbox.value())
        if mode is DeckSelectionMode.COVERAGE_PCT:
            return mode, self.coverage_spinbox.value()
        return mode, 0.0  # ignored for ALL

    def _from_superseded_worker(self) -> bool:
        """Whether the running slot was fired by a worker other than the current one.

        Worker signals cross threads queued, so one can land after a newer run
        has replaced its sender. A slot called directly -- including by the
        rollback in :meth:`_start`, which clears ``worker_thread`` first -- is
        never stale.
        """
        sender = self.sender()
        worker = self.worker_thread
        return sender is not None and worker is not None and sender is not worker

    # ------------------------------------------------------------------
    # Slots: Preview / Build / Cancel
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Scan the season and wait at the Build gate with its numbers."""
        self._start(confirm_now=False)

    def _on_build_clicked(self) -> None:
        """Confirm the live run, or start one already confirmed."""
        if self._run_state in ("scanning", "preview_ready"):
            self._confirm()
        elif self._run_state == "idle":
            self._start(confirm_now=True)

    def _start(self, confirm_now: bool) -> None:
        """Start a run: a scan that waits at the Build gate, or one already confirmed.

        Args:
            confirm_now: Confirm the current selection before the worker
                starts (Build from idle), so it never waits at the gate.
        """
        if still_running(self.worker_thread):
            return
        self.clear_screen_issue()
        request = self._build_request()
        if request is None:
            return
        # Refused here, before the worker creates the deck: folders that pair
        # no episodes must not leave an empty deck behind.
        if not FilePairMatcher.find_pairs_by_episode_number(request.video_folder, request.subtitle_folder):
            self.show_screen_issue(ScreenIssue(summary=self.tr("No video/subtitle pairs found. Check the folders.")))
            return

        self._cancel_requested = self._run_failed = self._run_had_item_failures = False
        self._corpus = self._confirmed_selection = None
        for label in self._result_labels.values():
            label.setText("—")
        self.progress_widget.reset()
        self.log_widget.clear_log()

        # Tear down any prior run before building the worker (Windows
        # back-to-back-mining freeze: leaked sqlite/Session handles).
        self._teardown_previous_run("deckbuilder")

        mode, value = self._current_selection()
        self._begin_receipt(
            1,
            item_noun=self.tr("deck"),
            run_fields={
                "deck": request.deck_name,
                "mode": mode.value,
                "value": value,
                "skip_known": request.skip_known,
                "review": request.review,
            },
        )
        self._publish_task_start(self.tr("Deck Builder"))

        # Construct, connect and start under one rollback, as Batch does: the
        # inputs are about to lock, and a failure anywhere in here would
        # otherwise leave them locked against a run that never began, with no
        # thread whose `finished` could ever unlock them.
        try:
            worker = DeckBuilderWorker(
                request,
                self.config,
                self.presenter,
                self.progress_callback,
                stats_service=self.stats_service,
                curation_callback=self._curation_bridge,
            )
            self.worker_thread = worker

            worker.preview_ready.connect(self._on_preview_ready)
            worker.item_pairs_progress.connect(self._on_item_pairs_progress)
            worker.item_completed.connect(self._on_item_completed)
            worker.item_failed.connect(self._on_item_failed)
            # Run-level fatals (stale-dict gate, deck creation, preflight)
            # emit error THEN queue_finished; the flag keeps the terminal line
            # from reading "Complete".
            worker.error.connect(self._on_worker_error)
            worker.queue_finished.connect(self._on_queue_finished)
            # The thread's end is the one signal every exit path sends, so it
            # is what returns the screen to idle and seals the receipt.
            worker.finished.connect(self._restore_buttons)
            worker.finished.connect(self._on_run_thread_finished)

            if confirm_now:
                self._confirm()
            worker.start()
        except Exception as exc:  # noqa: BLE001 - the run never began; surface and recover
            logger.exception("DeckBuilderTab failed to start the deck builder worker")
            self.worker_thread = None
            self._run_failed = True
            self._on_worker_error(str(exc))
            self._restore_buttons()
            self._on_run_thread_finished()
            return
        self._apply_run_state("building" if confirm_now else "scanning")

    def _confirm(self) -> None:
        """Confirm the current selection: build now, or as soon as the scan ends."""
        worker = self.worker_thread
        if worker is None:
            return
        if self._run_state == "preview_ready":
            # Leaving the Build gate: the "Preview ready..." status/detail it
            # set must not linger through the build that follows.
            self.progress_widget.set_status("")
            self._publish_task_detail("")
        mode, value = self._current_selection()
        self._confirmed_selection = (mode, value)
        worker.confirm(mode, value)
        self._apply_run_state("building")

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._on_cancel_clicked()

    def _on_cancel_clicked(self) -> None:
        """Cancel the run at any stage, the Build gate included; no prompt."""
        self._log_run_control("cancel")
        self._cancel_requested = True
        self._publish_task_cancelling()
        # Release any open curation dialog first so the worker doesn't hang (Issue #60).
        self._cancel_active_curation_dialog()
        if self.worker_thread is not None:
            # Also opens the Build gate, so a worker parked there ends now.
            self.worker_thread.cancel()
        # Nothing may confirm a cancelled run into a build on its way out;
        # the thread's end returns the screen to idle.
        self.build_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(self.tr("Cancelling…"))
        self.progress_widget.freeze()
        self.progress_widget.set_status(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Slots: worker signals
    # ------------------------------------------------------------------

    def _on_preview_ready(self, corpus: DeckCorpus) -> None:
        """Show the scan's numbers, and offer Build unless the run is already confirmed."""
        # Queued across threads, so it can land after Cancel: a cancelled run
        # must not come back as a preview waiting for Build.
        if self._from_superseded_worker() or self._cancel_requested:
            return
        self._corpus = corpus
        self._refresh_preview()
        if self._run_state == "scanning":
            self._apply_run_state("preview_ready")
            message = self.tr("Preview ready. Press Build Deck to create the cards.")
            self.progress_widget.set_status(message)
            self._publish_task_detail(message)

    def _refresh_preview(self, *_args: object) -> None:
        """Recompute the Results numbers from the cached corpus and the current selection.

        GUI-thread maths only: no rescan, no worker. Does nothing until a
        preview has arrived.
        """
        corpus = self._corpus
        if corpus is None:
            return
        mode, value = self._current_selection()
        preview = build_preview(corpus, rank_select(corpus.counts, mode, value))
        labels = self._result_labels
        labels["total_tokens"].setText(f"{preview.total_tokens:,}")
        labels["unique_lemmas"].setText(f"{preview.unique_lemmas:,}")
        labels["candidate_count"].setText(f"{preview.candidate_count:,}")
        labels["projected_coverage_pct"].setText(f"{preview.projected_coverage_pct:.1f}%")
        labels["known_skipped"].setText(f"{preview.known_skipped:,}")
        labels["card_count"].setText(f"{preview.card_count:,}")

    def _on_item_pairs_progress(self, _item_id: str, done: int, total: int) -> None:
        """Fill the bar by episodes mined, the one count the build can prove."""
        if self._from_superseded_worker() or total <= 0:
            return
        status = tr_format(self.tr("%1 of %2 episodes mined"), done, total)
        self.progress_widget.set_composed(done, total, status)
        self._publish_task_count(done, total, status)

    def _on_item_completed(self, _item_id: str, cards_created: int) -> None:
        """Record the deck's cards and write the closing line."""
        if self._from_superseded_worker():
            return
        self._record_receipt_counts(notes_added=cards_created, failed=False)
        self.presenter.show_success(self._closing_line(cards_created))

    def _on_item_failed(self, _item_id: str, error_message: str, cards_created: int) -> None:
        """Record a build that ended with episode failures, and say so."""
        if self._from_superseded_worker():
            return
        self._run_had_item_failures = True
        self._record_receipt_counts(notes_added=cards_created, failed=True)
        self.presenter.show_error(error_message)
        # Cards Anki already confirmed stay, so the closing line still counts them.
        self.presenter.show_info(self._closing_line(cards_created))
        self.show_screen_issue(ScreenIssue(summary=self.tr("Some episodes could not be mined."), details=error_message))

    def _on_worker_error(self, message: str) -> None:
        """Run-level fatal from the worker: flag it and surface it."""
        if self._from_superseded_worker():
            return
        self._run_failed = True
        self.presenter.show_error(message)
        self.show_screen_issue(ScreenIssue(summary=self.tr("The deck could not be built."), details=message))

    def _on_queue_finished(self, total_cards: int, whitelist: object = None) -> None:
        """Fold the run's whitelist into the receipt and draw the terminal progress line."""
        if self._from_superseded_worker():
            return
        self._record_receipt_whitelist(whitelist)
        if self._cancel_requested and total_cards > 0:
            # The item interrupted mid-build emits neither item_completed nor
            # item_failed, so this is the only place its real card count
            # reaches the log.
            self.presenter.show_info(self._closing_line(total_cards))
        self._show_terminal_progress(self.progress_widget, total_cards)

    def _restore_buttons(self) -> None:
        """Return the screen to idle once the run's thread has ended."""
        if self._from_superseded_worker():
            return
        self._apply_run_state("idle")

    def _closing_line(self, cards_created: int) -> str:
        """The Activity Log's closing line: cards, deck, and coverage when known.

        The coverage is the preview's projection for the confirmed selection,
        so it is attributed to the candidate words, not to the cards: known
        words count toward it, and a word with no definition gets no card. A
        run with no preview (every episode failed the scan) has none to quote.
        """
        worker = self.worker_thread
        deck_name = worker.request.deck_name if worker is not None else ""
        cards = f"{cards_created:,}"
        corpus = self._corpus
        if corpus is not None and self._confirmed_selection is not None:
            mode, value = self._confirmed_selection
            coverage = build_preview(corpus, rank_select(corpus.counts, mode, value)).projected_coverage_pct
            return tr_format(
                self.tr("Created %1 cards in deck '%3'; the candidate words cover ~%2% of tokens."),
                cards,
                f"{coverage:.1f}",
                deck_name,
            )
        return tr_format(self.tr("Created %1 cards in deck '%2'."), cards, deck_name)

    def release_dictionary_resources(self) -> bool:
        """Close sqlite handles cached by the most recent run (Issue #30/#32).

        ``DeckBuilderWorker`` exposes its retained processor via the typed
        ``curation_processor`` property it inherits from Batch's worker. The
        handle is still open after the run finishes and blocks Settings →
        Remove / Re-import on Windows.

        Returns ``False`` while a worker is running -- one parked at the Build
        gate included, since its processor is still in use -- because closing
        providers under an in-flight processor would crash the run. The facade
        resets the chain so the next run re-opens it cleanly.
        """
        if still_running(self.worker_thread):
            return False
        if self.worker_thread is not None:
            proc = self.worker_thread.curation_processor
            if proc is not None:
                proc.release_dictionary_resources()
        return True

    # ------------------------------------------------------------------
    # Durable queue contents (D16-C)
    # ------------------------------------------------------------------

    def queue_snapshot(self) -> QueueSnapshot:
        """Describe the in-flight build, or an empty snapshot when nothing is running.

        A row exists only while ``_run_state == "building"``: a scan or a
        preview waiting at the Build gate has written nothing to Anki, so
        losing it costs the user a rescan and nothing else. A confirmed build
        may already have cards in the deck -- including one whose Cancel is
        still draining (Task 7: ``_run_state`` holds "building" until
        ``finished`` arrives) -- so that is the one case worth restoring the
        form for. The worker's own ``request`` is the single source of truth
        for what this run started with; there is nothing to re-derive from
        the (locked, but still live) input widgets.
        """
        if self._run_state != "building" or self.worker_thread is None:
            return QueueSnapshot(key=self.QUEUE_STATE_KEY, items=())
        request = self.worker_thread.request
        return QueueSnapshot(
            key=self.QUEUE_STATE_KEY,
            items=(
                QueueItemSnapshot(
                    item_id="build",
                    source=queue_state_store.folder_pair_source(
                        request.video_folder,
                        request.subtitle_folder,
                        offset=request.subtitle_offset,
                        secondary=request.secondary_folder,
                        secondary_offset=request.secondary_offset,
                    ),
                    title=request.deck_name,
                    status=queue_state_store.status_from_run_state("processing"),
                ),
            ),
        )

    def restore_queue_snapshot(self, snapshot: QueueSnapshot) -> int:
        """Refill the form from ``snapshot``'s one row; return the row count.

        Refused outside idle (a real launch never calls this any other way).
        The folders are refilled even when one has since moved, so the user
        sees exactly what the interrupted build was pointed at; the banner
        then says which problem it is: the input gone, or the build itself
        interrupted. Nothing here starts a run -- the user's own Build Deck
        does that, after which words already carded are skipped.
        """
        if self._run_state != "idle" or not snapshot.items:
            return 0
        row = snapshot.items[0]
        source = row.source
        self.video_folder_selector.set_path(str(source["video"]))
        self.subtitle_folder_selector.set_path(str(source["subtitle"]))
        self.offset_spinbox.setValue(float(source.get("offset", 0.0) or 0.0))
        secondary_raw = source.get("secondary")
        if isinstance(secondary_raw, str) and secondary_raw:
            self.secondary_folder_selector.set_path(secondary_raw)
            self.secondary_offset_spinbox.setValue(float(source.get("secondary_offset", 0.0) or 0.0))
        # After the folder selectors, which auto-fill the deck name from the
        # video folder's basename (_on_video_folder_changed): the restored
        # title always has the final word.
        self.deck_name_edit.setText(row.title)

        missing = row.missing_paths()
        if missing:
            self.show_screen_issue(ScreenIssue(summary=tr_format(self.tr("Folder not found: %1"), str(missing[0]))))
        else:
            self.show_screen_issue(
                ScreenIssue(
                    summary=tr_format(
                        self.tr(
                            "The build into deck '%1' was interrupted when Anki Miner closed. Build Deck "
                            "again to finish it; words already in the deck are skipped."
                        ),
                        row.title,
                    )
                )
            )
        return 1

    def clear_queue(self) -> None:
        """Public alias for Clear, for the language switch (D16-C).

        This screen has no queue panel -- its one "queued" thing is the form
        itself -- so clearing it means the same as a fresh, untouched screen:
        every input blanked, the cached preview dropped, and any restore
        banner dismissed. A switch only reaches this with no worker running.
        """
        self.clear_screen_issue()
        self.video_folder_selector.clear()
        self.subtitle_folder_selector.clear()
        self.secondary_folder_selector.clear()
        self.offset_spinbox.setValue(self.config.subtitle_offset)
        self.secondary_offset_spinbox.setValue(0.0)
        self.deck_name_edit.clear()
        self._last_auto_deck_name = ""
        self._corpus = None
        self._confirmed_selection = None
        for label in self._result_labels.values():
            label.setText("—")

    # ------------------------------------------------------------------
    # Config update
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration.

        Args:
            config: New configuration
        """
        # The offset spinbox is a per-session value the user dials in for the
        # next run; it is never persisted back to config. Only follow
        # config.subtitle_offset when the *persisted* value actually changed,
        # so an unrelated settings save / theme toggle (each of which re-fires
        # update_config) doesn't wipe the in-progress offset. Mirrors
        # BatchProcessingTab.update_config.
        if config.subtitle_offset != self.config.subtitle_offset:
            self.offset_spinbox.setValue(config.subtitle_offset)
        self.config = config
        self._apply_secondary_gate()
        self._seed_selection_controls()
        self._seed_review_words_checkbox()

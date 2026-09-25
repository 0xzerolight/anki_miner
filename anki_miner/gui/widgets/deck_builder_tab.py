"""Deck Builder tab: mine one show's season into a single named Anki deck.

Built on Batch's season pipeline: :class:`DeckBuilderWorker` (a
``BatchQueueWorkerThread`` subclass) mines the folder pair as one season item
under a build-only config, gated on a corpus preview before the actual build
runs. This module owns the screen's inputs and the request they build --
Preview, Build and Cancel are wired to the worker in a later task, so their
slots are stubs here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
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
from anki_miner.models.deck_build import DeckBuildRequest, DeckSelectionMode

if TYPE_CHECKING:
    from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker

#: Shared by every folder picker on this screen (D7): Browse reopens where the
#: last one of these three left off, independent of Batch's own history.
_HISTORY_KEY = "video.deckbuilder.inputs"


class DeckBuilderTab(FolderSeriesScreenBase):
    """Mine one show's video/subtitle folders into a single named Anki deck.

    Subclasses :class:`FolderSeriesScreenBase` for the same folder-pair inputs
    Batch's Add Series card uses (video, subtitle, optional translation
    folder, offsets), then adds the deck name and word-selection controls a
    season build needs. The corpus preview and the actual build both run on
    :class:`DeckBuilderWorker` (wired in a later task); this class owns the
    inputs, their validation, and the request they build.
    """

    #: Tables of results and log lines genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the pinned
    #: bar gets a stage and a progress bar (D17, D22), mirroring Batch.
    TASK_ID = "run.deckbuilder"
    TASK_OWNER = CapabilityTarget("video", "deckbuilder")

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
        # it. Preview/Build/Cancel are stubs here -- a later task wires them
        # to DeckBuilderWorker.
        self.preview_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_button.setToolTip(self.tr("Scan the season and preview which words will be included"))
        self.preview_button.clicked.connect(self._on_preview_clicked)

        self.build_button = ModernButton(self.tr("Build Deck"), variant="primary")
        self.build_button.setToolTip(self.tr("Create the Anki cards for the previewed word list"))
        self.build_button.setEnabled(False)
        self.build_button.clicked.connect(self._on_build_clicked)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.setEnabled(False)
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

        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
            file_filter="",
            label_width=label_w,
            history_key=_HISTORY_KEY,
        )
        layout.addWidget(self.subtitle_folder_selector)

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

        section.setLayout(layout)
        return section

    def _create_results_section(self) -> QFrame:
        """Build the Results card: the corpus-preview numbers Task 7 fills in."""
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
        starts no worker; Preview and Build (wired in a later task) both read
        this and stop the same way. Whether the folders actually pair up any
        episodes is the worker's own preflight, not this screen's.
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
    # Slots: Preview / Build / Cancel -- stubs; a later task wires the worker.
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Stub: a later task starts the corpus-preview worker from here."""

    def _on_build_clicked(self) -> None:
        """Stub: a later task confirms the gated worker's build phase from here."""

    def _on_cancel_clicked(self) -> None:
        """Stub: a later task wires worker cancellation to this button."""

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

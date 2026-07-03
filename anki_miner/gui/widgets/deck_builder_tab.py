"""Deck Builder tab — mine an entire series into a named Anki deck."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

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
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker
from anki_miner.models.deck_build import DeckBuildPreview, DeckBuildRequest, DeckSelectionMode
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class DeckBuilderTab(MiningTabBase):
    """Tab that mines an entire video folder into a named Anki deck.

    Two-phase flow driven by :class:`DeckBuilderWorker`:

    1. User selects folders, chooses a mode, and clicks **Preview** — the worker
       aggregates the corpus off-thread and emits a :class:`DeckBuildPreview`.
    2. The preview numbers are shown.  The user then clicks **Build Deck** — the
       worker's confirm gate is opened and Phase 2 (actual mining) begins.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback,
        stats_service=None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.stats_service = stats_service

        self.worker_thread: DeckBuilderWorker | None = None
        # Tracks the last value auto-filled from the video folder name so we can
        # distinguish "user typed something" from "still showing auto value".
        self._last_auto_deck_name: str = ""

        self._wire_progress_callback(self.progress_callback)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        # Shared label-column width so every labeled row across both cards
        # lines its input field up at the same x.
        self._label_w = field_label_width(
            "Video Folder:",
            "Subtitle Folder:",
            "Deck Name:",
            "Word Selection:",
        )

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_input_section())
        layout.addWidget(self._create_settings_section())
        layout.addWidget(self._create_actions_section())
        layout.addWidget(self._create_results_section())

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

        # Connect presenter signals to log widget (mirrors single_episode_tab)
        self.presenter.info_signal.connect(self.log_widget.append_info)
        self.presenter.success_signal.connect(self.log_widget.append_success)
        self.presenter.warning_signal.connect(self.log_widget.append_warning)
        self.presenter.error_signal.connect(self.log_widget.append_error)

    def _create_input_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Input")))

        self.video_folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
            placeholder=self.tr("Select folder with video files…"),
            label_width=self._label_w,
        )
        layout.addWidget(self.video_folder_selector)

        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
            placeholder=self.tr("Select folder with subtitle files…"),
            label_width=self._label_w,
        )
        layout.addWidget(self.subtitle_folder_selector)

        # Auto-fill deck name from folder name
        self.video_folder_selector.path_changed.connect(self._on_video_folder_changed)

        group.setLayout(layout)
        return group

    def _create_settings_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Deck Settings")))

        # Deck name row
        deck_row = QHBoxLayout()
        deck_row.setSpacing(SPACING.xs)
        deck_label = QLabel(self.tr("Deck Name:"))
        deck_label.setObjectName("field-label")
        deck_label.setFixedWidth(self._label_w)
        deck_row.addWidget(deck_label)
        self.deck_name_edit = QLineEdit()
        self.deck_name_edit.setPlaceholderText(self.tr("Enter deck name…"))
        deck_row.addWidget(self.deck_name_edit, 1)
        layout.addLayout(deck_row)

        # Mode row
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_label = QLabel(self.tr("Word Selection:"))
        mode_label.setObjectName("field-label")
        mode_label.setFixedWidth(self._label_w)
        mode_row.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.tr("All vocabulary"), userData=DeckSelectionMode.ALL)
        self.mode_combo.addItem(self.tr("Top N words"), userData=DeckSelectionMode.TOP_N)
        self.mode_combo.addItem(self.tr("Target coverage %"), userData=DeckSelectionMode.COVERAGE_PCT)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Value inputs (one per relevant mode; only one visible at a time)
        value_row = QHBoxLayout()
        value_row.setSpacing(SPACING.xs)
        value_row.addSpacing(self._label_w + SPACING.xs)  # align under the combo

        self.top_n_spinbox = QSpinBox()
        self.top_n_spinbox.setRange(1, 100_000)
        self.top_n_spinbox.setValue(1000)
        self.top_n_spinbox.setSuffix(self.tr(" words"))
        self.top_n_spinbox.setToolTip(self.tr("Include the N most-frequent lemmas"))
        value_row.addWidget(self.top_n_spinbox)

        self.coverage_spinbox = QDoubleSpinBox()
        self.coverage_spinbox.setRange(1.0, 100.0)
        self.coverage_spinbox.setValue(90.0)
        self.coverage_spinbox.setDecimals(1)
        self.coverage_spinbox.setSuffix(" %")
        self.coverage_spinbox.setToolTip(self.tr("Include enough words to cover this percentage of tokens"))
        value_row.addWidget(self.coverage_spinbox)
        self.coverage_spinbox.hide()

        value_row.addStretch()
        layout.addLayout(value_row)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        # Trigger once to set initial visibility (index 0 = ALL → hide both)
        self._on_mode_changed(0)

        # Collection filter
        self.collection_filter_checkbox = QCheckBox(self.tr("Skip words already in my Anki collection"))
        self.collection_filter_checkbox.setChecked(True)
        self.collection_filter_checkbox.setToolTip(
            self.tr("Checked: skip your known words; unchecked: mine every word.")
        )
        layout.addWidget(self.collection_filter_checkbox)

        group.setLayout(layout)
        return group

    def _create_actions_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Actions")))

        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.xs)

        self.preview_button = ModernButton(self.tr("Preview"), variant="secondary")
        self.preview_button.setToolTip(self.tr("Analyze the corpus and preview which words will be included"))

        self.build_button = ModernButton(self.tr("Build Deck"), variant="primary")
        self.build_button.setToolTip(self.tr("Create the Anki cards for the previewed word list"))
        self.build_button.setEnabled(False)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="danger")
        self.cancel_button.setToolTip(self.tr("Cancel the current operation"))
        self.cancel_button.setEnabled(False)

        self.preview_button.clicked.connect(self._on_preview_clicked)
        self.build_button.clicked.connect(self._on_build_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)

        button_layout.addWidget(self.preview_button)
        button_layout.addWidget(self.build_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        group.setLayout(layout)
        return group

    def _create_results_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Results")))

        # Preview summary area (hidden until a preview arrives)
        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("card")
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(SPACING.sm, SPACING.sm, SPACING.sm, SPACING.sm)
        preview_layout.setSpacing(SPACING.xs)

        self._preview_labels: dict[str, QLabel] = {}
        for field_key, field_label in [
            ("total_tokens", self.tr("Total tokens:")),
            ("unique_lemmas", self.tr("Unique lemmas:")),
            ("candidate_count", self.tr("Candidate words:")),
            ("projected_coverage_pct", self.tr("Projected coverage:")),
            ("known_skipped", self.tr("Known (skipped):")),
            ("card_count", self.tr("Cards to create:")),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(field_label)
            lbl.setObjectName("field-label")
            lbl.setMinimumWidth(160)
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._preview_labels[field_key] = val
            row.addWidget(lbl)
            row.addWidget(val, 1)
            preview_layout.addLayout(row)

        self.preview_frame.setLayout(preview_layout)
        self.preview_frame.hide()
        layout.addWidget(self.preview_frame)

        # Progress widget
        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

        # Log widget
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    # Slot: mode combo change
    # ------------------------------------------------------------------

    def _on_mode_changed(self, index: int) -> None:
        """Show/hide the value input appropriate for the selected mode."""
        mode = self.mode_combo.itemData(index)
        self.top_n_spinbox.setVisible(mode == DeckSelectionMode.TOP_N)
        self.coverage_spinbox.setVisible(mode == DeckSelectionMode.COVERAGE_PCT)

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
    # Slot: Preview button
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Validate inputs, build a request, and start Phase 1."""
        video_folder = self.video_folder_selector.get_path().strip()
        subtitle_folder = self.subtitle_folder_selector.get_path().strip()

        if not video_folder or not subtitle_folder:
            self.log_widget.append_warning(self.tr("Select both the video folder and subtitle folder first."))
            return

        if not self.video_folder_selector.is_valid():
            self.log_widget.append_warning(tr_format(self.tr("Video folder not found: %1"), video_folder))
            return

        if not self.subtitle_folder_selector.is_valid():
            self.log_widget.append_warning(tr_format(self.tr("Subtitle folder not found: %1"), subtitle_folder))
            return

        deck_name = self.deck_name_edit.text().strip()
        if not deck_name:
            self.log_widget.append_warning(self.tr("Enter a deck name before previewing."))
            return

        pairs = FilePairMatcher.find_pairs_by_episode_number(Path(video_folder), Path(subtitle_folder))
        if not pairs:
            self.log_widget.append_warning(self.tr("No video/subtitle pairs found. Check the folders."))
            return

        mode: DeckSelectionMode = self.mode_combo.currentData()
        if mode == DeckSelectionMode.TOP_N:
            value: float = float(self.top_n_spinbox.value())
        elif mode == DeckSelectionMode.COVERAGE_PCT:
            value = self.coverage_spinbox.value()
        else:
            value = 0.0  # ignored for ALL

        request = DeckBuildRequest(
            pairs=pairs,
            deck_name=deck_name,
            mode=mode,
            value=value,
            collection_filter=self.collection_filter_checkbox.isChecked(),
        )

        # Defensive: cancel any lingering worker before replacing it. In normal
        # flow Preview is disabled while a worker is gated or building, so this
        # is rarely hit — but if it is, disconnect the old worker's finished
        # handler first so its termination does not restore buttons mid-new-run.
        if self.worker_thread is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                self.worker_thread.finished.disconnect(self._restore_buttons)
            self.worker_thread.cancel()
            # Bounded join: cancel() unblocks the confirm gate / active
            # processor, so the thread winds down quickly. Reassigning
            # self.worker_thread below would otherwise drop the only reference
            # to a live QThread — "QThread: Destroyed while thread is still
            # running" — and crash. Bounded so a stuck worker cannot freeze
            # the GUI forever.
            joined = self.worker_thread.wait(5000)
            if not joined:
                logger.warning("Lingering deck-builder worker did not stop within 5 s; replacing it anyway")
            # Release the survivor processor's sqlite handles + HTTP session
            # before dropping the worker reference on reassignment below; single
            # and batch tabs do the same in _teardown_previous_run. Only when
            # joined: never close() under a still-running worker.
            old_processor = self.worker_thread.curation_processor
            if joined and old_processor is not None:
                with contextlib.suppress(Exception):
                    old_processor.close()

        self.log_widget.clear_log()
        self.log_widget.append_info(self.tr("Analyzing corpus…"))
        self.preview_frame.hide()
        self._set_buttons_running()

        self.worker_thread = DeckBuilderWorker(
            request, self.config, self.presenter, self.progress_callback, self.stats_service
        )
        self.worker_thread.preview_ready.connect(self._on_preview_ready)
        self.worker_thread.item_started.connect(self._on_item_started)
        self.worker_thread.item_completed.connect(self._on_item_completed)
        self.worker_thread.build_finished.connect(self._on_build_finished)
        self.worker_thread.error.connect(self._on_error)
        # Restore buttons only when the QThread truly finishes. The worker
        # reference is NOT nulled in the terminal slots: dropping the only
        # reference while the QThread is still unwinding risks
        # "QThread: Destroyed while thread is still running". It is replaced on
        # the next preview instead.
        self.worker_thread.finished.connect(self._restore_buttons)
        self.worker_thread.start()

    # ------------------------------------------------------------------
    # Slot: preview_ready (Phase 1 complete; worker blocked on gate)
    # ------------------------------------------------------------------

    def _on_preview_ready(self, preview: DeckBuildPreview) -> None:
        self._preview_labels["total_tokens"].setText(f"{preview.total_tokens:,}")
        self._preview_labels["unique_lemmas"].setText(f"{preview.unique_lemmas:,}")
        self._preview_labels["candidate_count"].setText(f"{preview.candidate_count:,}")
        self._preview_labels["projected_coverage_pct"].setText(f"{preview.projected_coverage_pct:.1f}%")
        self._preview_labels["known_skipped"].setText(f"{preview.known_skipped:,}")
        self._preview_labels["card_count"].setText(f"{preview.card_count:,}")
        self.preview_frame.show()

        self.log_widget.append_success(
            tr_format(
                self.tr("Preview ready — %1 cards, ~%2% coverage. Click 'Build Deck' to proceed."),
                f"{preview.card_count:,}",
                f"{preview.projected_coverage_pct:.1f}",
            )
        )

        # Enable Build; keep Cancel so the user can abandon the gated worker
        self.build_button.setEnabled(True)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Slot: Build Deck button
    # ------------------------------------------------------------------

    def _on_build_clicked(self) -> None:
        if self.worker_thread is None:
            return
        self.build_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        deck_name = self.worker_thread.request.deck_name
        self.log_widget.append_info(tr_format(self.tr("Building deck '%1'…"), deck_name))
        self.worker_thread.confirm()

    # ------------------------------------------------------------------
    # Slots: per-episode progress
    # ------------------------------------------------------------------

    def _on_item_started(self, name: str) -> None:
        self.progress_widget.set_status(tr_format(self.tr("Processing: %1"), name))
        self.log_widget.append_info(tr_format(self.tr("Processing: %1"), name))

    def _on_item_completed(self, name: str, cards: int) -> None:
        self.log_widget.append_info(tr_format(self.tr("  %1: %2 card(s) created"), name, cards))

    # ------------------------------------------------------------------
    # Slot: build_finished (Phase 2 complete)
    # ------------------------------------------------------------------

    def _on_build_finished(self, total: int, coverage: float) -> None:
        deck_name = self.worker_thread.request.deck_name if self.worker_thread else "deck"
        # ``coverage`` is the Phase-1 projection over the candidate set (known
        # words count toward it); the actual card count can be lower when known
        # words are skipped or a definition lookup yields nothing, so this is
        # framed as a target rather than an achieved figure.
        self.log_widget.append_success(
            tr_format(
                self.tr("Done! Created %1 cards (~%2% target coverage) in deck '%3'."),
                f"{total:,}",
                f"{coverage:.1f}",
                deck_name,
            )
        )
        self.progress_widget.set_status(self.tr("Build complete"))
        self._restore_buttons()

    # ------------------------------------------------------------------
    # Slot: Cancel button
    # ------------------------------------------------------------------

    def _on_cancel_clicked(self) -> None:
        # Request cancellation and reflect the in-progress state. Buttons are
        # restored by the worker's ``finished`` signal once the thread actually
        # ends — restoring Preview here would let a new run reassign
        # ``self.worker_thread`` over a still-running thread.
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling…"))
        self.cancel_button.setEnabled(False)
        self.log_widget.append_warning(self.tr("Cancelling…"))

    # ------------------------------------------------------------------
    # Slot: error (from worker)
    # ------------------------------------------------------------------

    def _on_error(self, msg: str) -> None:
        self.log_widget.append_error(tr_format(self.tr("Error: %1"), msg))
        self._restore_buttons()

    # Progress slots (_on_progress_start/update/complete) are inherited from
    # MiningTabBase — the percentage-scaled ``set_progress`` path. The old
    # ``set_value(current)`` overrides clamped the bar to 100% past item 100
    # (e.g. a 2,401-card build pinned at 100% during media extraction).

    # ------------------------------------------------------------------
    # Button-state helpers
    # ------------------------------------------------------------------

    def _set_buttons_running(self) -> None:
        """Disable Preview + Build; enable Cancel."""
        self.preview_button.setEnabled(False)
        self.build_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

    def _restore_buttons(self) -> None:
        """Re-enable Preview; disable Build + Cancel; reset Cancel label."""
        self.preview_button.setEnabled(True)
        self.build_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(self.tr("Cancel"))

    # ------------------------------------------------------------------
    # Config update
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        self.config = config

    def release_dictionary_resources(self) -> bool:
        """Close sqlite handles cached by the most recent build (Issue #30/#32).

        Phase 2 retains the last per-episode processor (exposed via the
        worker's typed ``curation_processor`` property, with open
        ``index.sqlite`` handles) until a new run replaces the worker. On
        Windows those handles keep the dictionary folder locked, so
        Settings -> Remove / Re-import fails after a build until the app is
        restarted.

        Returns ``False`` while a worker is actively running — Phase 1 (aggregate)
        or Phase 2 (build) — because closing providers under an in-flight
        processor would crash the run; the caller surfaces a clear "in progress"
        message instead of silently dropping the dict. The facade resets the
        chain so the next build re-opens it cleanly.
        """
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return False
        if self.worker_thread is not None:
            proc = self.worker_thread.curation_processor
            if proc is not None:
                proc.release_dictionary_resources()
        return True

"""Subtitle Retime tab — retime subtitle files against their videos.

Composes four :class:`~anki_miner.gui.widgets.enhanced.FileSelector` instances
(single-file / folder mode toggle — video + subtitle selectors per mode), a
reference row, an output-location row, an Overwrite checkbox, a Retime button, a
:class:`~anki_miner.gui.widgets.progress_widget.ProgressWidget` for overall queue
progress, and a :class:`~anki_miner.gui.widgets.log_widget.LogWidget` for
per-pair pass/fail lines.

There are no alignment knobs here or anywhere: the retime pipeline
(services/subtitle_retimer.py) tunes itself — engine chain (ffsubsync, then
alass, then ffsubsync again), dialogue-only cleaning, and result validation
with a keep-original guarantee. The one decision on this screen is which
files.

Guard contract:
- alass not found → notice visible; retiming stays enabled (ffsubsync-only).
- Output directory not writable → Retime aborts, error logged.

Worker contract:
- Worker stored on ``self.worker_thread``.
- ``iter_close_workers()`` yields the active worker for
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.constants import SUBTITLE_FILE_FILTER, VIDEO_FILE_FILTER
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import reveal_settings
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout
from anki_miner.gui.widgets.dialogs import RetimeReferenceDialog, build_reference_choices
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.workers.subtitle_retime_worker import SubtitleRetimeWorker
from anki_miner.services.retime_reference import list_reference_subtitle_streams
from anki_miner.utils import list_audio_streams
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.widgets.dialogs import ReferenceChoice
    from anki_miner.services.retime_reference import ReferenceOverride
    from anki_miner.utils.file_pairing import FilePair

logger = logging.getLogger(__name__)


class SubtitleRetimeTab(_ToolTabBase):
    """Tab for retiming subtitle files to video using alass.

    Shared worker-signal slots, output-location slots, progress chrome, and the
    close contract live in :class:`~anki_miner.gui.widgets._tool_tab_base._ToolTabBase`.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "tools.retime"
    TASK_OWNER = CapabilityTarget("subtitles", "retime")

    #: Where this tool last wrote — remembered separately from its inputs (D7).
    OUTPUT_HISTORY_KEY = "tools.retime.output"

    def __init__(
        self,
        config: AnkiMinerConfig,
        parent: QWidget | None = None,
        *,
        suppress_optional_startup: bool = False,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._suppress_optional_startup = suppress_optional_startup
        self.worker_thread = None
        self._custom_output_dir: Path | None = None
        self._total_pairs: int = 0
        self._cancelled: bool = False
        # Per-run reference selection for single-file mode (an embedded subtitle
        # or audio track), or None for auto. Reset when the video changes.
        self._reference_override: ReferenceOverride | None = None
        # alass availability is cached per-config: probing it (resolve_alass +
        # shutil.which / Path.exists) is a PATH scan we must not repeat on every
        # _alass_available() read. Recomputed only here and in update_config().
        self._alass_is_available: bool = False
        # Built here (not in the base) so each literal stays in this tab's
        # tr-context — see _ToolTabBase for the rationale.
        self._strings = _ToolTabStrings(
            progress=self.tr("Progress"),
            done=self.tr("Done"),
            done_prefix=self.tr("Done: "),
            skipped=self.tr("Skipped"),
            skipped_prefix=self.tr("Skipped: "),
            cancel=self.tr("Cancel"),
            cancelling=self.tr("Cancelling…"),
            cancelled=self.tr("Cancelled"),
            failed=self.tr("Failed — see log"),
            run_problem=self.tr("Some files could not be retimed."),
            complete_template=self.tr("Complete — %1 files processed"),
            complete_skipped_template=self.tr("Complete — %1 processed, %2 skipped"),
            all_skipped_template=self.tr(
                "No files retimed — all %1 skipped. Enable Overwrite to replace the existing "
                "retimed files, or choose a different output folder."
            ),
            select_output_folder=self.tr("Select Output Folder"),
            output_default=self.tr("Next to source video"),
            task_title=self.tr("Subtitle retiming"),
        )

        self._setup_ui()
        self._refresh_engine_state()

    def _item_total(self) -> int:
        return self._total_pairs

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new application config (e.g. after the alass path changes).

        Wired to ``settings_tab.config_changed`` and ``window.config_refreshed``
        so a path change in Settings is reflected in the availability guard.
        A run already in flight keeps the config it captured at construction.
        """
        self.config = config
        # A config change is exactly when alass can appear/disappear (in-app
        # download flips alass_location/bin_root, or the user edits the path),
        # so recompute the cache BEFORE _refresh_engine_state reads it.
        self._refresh_engine_state()

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
        layout.addWidget(self._create_output_section())
        self._create_action_buttons()
        layout.addWidget(self._create_progress_section())
        layout.addStretch()

        container.setLayout(layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._install_action_bar(main_layout, scroll_area, container, self.PAGE_WIDTH)
        self.setLayout(main_layout)
        self.install_issue_banner(main_layout)

    def _create_input_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Input")))

        # alass notice (shown when alass unavailable; retiming still works)
        self.engine_notice_label = QLabel(
            self.tr("alass not found; retiming uses ffsubsync only. Install alass in Settings for a fallback engine.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        # Input description
        input_desc = QLabel(self.tr("Resync a subtitle file to its video by matching audio."))
        input_desc.setObjectName("helper-text")
        input_desc.setWordWrap(True)
        layout.addWidget(input_desc)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_label = QLabel(self.tr("Mode:"))
        mode_row.addWidget(mode_label)

        self.file_mode_button = ModernButton(self.tr("Single File"), variant="secondary")
        self.file_mode_button.setCheckable(True)
        self.file_mode_button.setChecked(True)
        self.file_mode_button.setToolTip(self.tr("Retime one subtitle file against one video."))
        self.file_mode_button.clicked.connect(self._on_file_mode)
        mode_row.addWidget(self.file_mode_button)

        self.folder_mode_button = ModernButton(self.tr("Folder"), variant="secondary")
        self.folder_mode_button.setCheckable(True)
        self.folder_mode_button.setChecked(False)
        self.folder_mode_button.setToolTip(self.tr("Retime a folder of subtitles, paired to videos by episode number."))
        self.folder_mode_button.clicked.connect(self._on_folder_mode)
        mode_row.addWidget(self.folder_mode_button)

        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Single-mode selectors
        self.video_file_selector = FileSelector(
            label=self.tr("Video File:"),
            file_mode=True,
            file_filter=VIDEO_FILE_FILTER,
            history_key="tools.retime.inputs",
            drop_validator=accepts_suffixes(
                FilePairMatcher.VIDEO_EXTENSIONS, self.tr("This field takes a video file.")
            ),
        )
        layout.addWidget(self.video_file_selector)

        self.subtitle_file_selector = FileSelector(
            label=self.tr("Subtitle File:"),
            file_mode=True,
            file_filter=SUBTITLE_FILE_FILTER,
            history_key="tools.retime.inputs",
            drop_validator=accepts_suffixes(
                FilePairMatcher.SUBTITLE_EXTENSIONS, self.tr("This field takes a subtitle file.")
            ),
        )
        layout.addWidget(self.subtitle_file_selector)

        # Reset the reference override whenever the video changes (selection is
        # per-run and must not silently carry over to a different file).
        self.video_file_selector.path_changed.connect(self._on_video_path_changed)

        # Single-mode reference override row. One row for both kinds of
        # reference: alass takes an embedded subtitle track or audio, and which
        # of the two is used is an implementation detail the user only overrides
        # when auto-selection picks badly.
        self.track_row_widget = QWidget()
        track_row = QHBoxLayout(self.track_row_widget)
        track_row.setContentsMargins(0, 0, 0, 0)
        track_row.setSpacing(SPACING.xs)
        track_row.addWidget(QLabel(self.tr("Align against:")))
        self.reference_label = QLabel(self._auto_reference_text())
        self.reference_label.setObjectName("output-location-value")
        track_row.addWidget(self.reference_label, 1)
        self.tracks_button = ModernButton(self.tr("Change…"), variant="secondary")
        self.tracks_button.setToolTip(self.tr("Choose which embedded track to align the subtitle against."))
        self.tracks_button.clicked.connect(self._on_tracks_clicked)
        track_row.addWidget(self.tracks_button)
        layout.addWidget(self.track_row_widget)

        # Folder-mode selectors (hidden by default)
        self.video_folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
            history_key="tools.retime.inputs",
        )
        self.video_folder_selector.hide()
        layout.addWidget(self.video_folder_selector)

        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
            history_key="tools.retime.inputs",
        )
        self.subtitle_folder_selector.hide()
        layout.addWidget(self.subtitle_folder_selector)

        # Pair preview (folder mode): shows exactly which subtitle each video
        # will be paired with BEFORE the run, so a mispairing (the silent
        # destroyer of whole-season retimes) is visible up front.
        self.pair_preview_label = QLabel(self.tr("Matched pairs:"))
        self.pair_preview_label.hide()
        layout.addWidget(self.pair_preview_label)
        self.pair_preview = QListWidget()
        self.pair_preview.setObjectName("pair-preview")
        self.pair_preview.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.pair_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pair_preview.setMaximumHeight(180)
        self.pair_preview.hide()
        layout.addWidget(self.pair_preview)
        self.video_folder_selector.path_changed.connect(lambda _p: self._refresh_pair_preview())
        self.subtitle_folder_selector.path_changed.connect(lambda _p: self._refresh_pair_preview())

        group.setLayout(layout)
        return group

    def _refresh_pair_preview(self) -> None:
        """Recompute and show the folder-mode video↔subtitle pairing preview.

        The scan (FilePairMatcher + a directory listing) runs off the GUI
        thread — it can stall on a network share — and its result is dropped
        if the folder selection changed while it was in flight.
        """
        if self.video_folder_selector.isHidden():
            self.pair_preview.hide()
            self.pair_preview_label.hide()
            return
        video_folder_str = self.video_folder_selector.path_or_none()
        sub_folder_str = self.subtitle_folder_selector.path_or_none()
        if video_folder_str is None or sub_folder_str is None:
            self.pair_preview.hide()
            self.pair_preview_label.hide()
            return

        video_folder = Path(video_folder_str)
        sub_folder = Path(sub_folder_str)

        def _scan() -> object:
            # prefer_retimed=False: mining wants the retimed subtitle, this tab
            # wants the one it was made from. Without it a second run over the
            # same folder would retime its own output.
            pairs = FilePairMatcher.find_pairs_by_episode_number(video_folder, sub_folder, prefer_retimed=False)
            try:
                unmatched = sorted(
                    f.name
                    for f in video_folder.iterdir()
                    if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
                )
            except OSError:
                unmatched = []
            return pairs, unmatched

        def _apply(result: object) -> None:
            # Stale guard: the folder selection may have changed mid-scan.
            if self.video_folder_selector.path_or_none() != video_folder_str:
                return
            if self.subtitle_folder_selector.path_or_none() != sub_folder_str:
                return
            pairs, unmatched = cast("tuple[list, list[str]]", result)
            self.pair_preview.clear()
            for pair in pairs:
                self.pair_preview.addItem(f"{pair.video.name}  ←  {pair.subtitle.name}")
            matched_names = {pair.video.name for pair in pairs}
            for name in unmatched:
                if name not in matched_names:
                    self.pair_preview.addItem(tr_format(self.tr("%1  —  no matching subtitle"), name))

            self.pair_preview_label.setText(tr_format(self.tr("Matched pairs (%1):"), str(len(pairs))))
            self.pair_preview_label.show()
            self.pair_preview.show()

        def _on_error(_msg: str) -> None:
            # Stale guard, same as _apply. A failed scan must not leave a
            # PREVIOUS successful scan's pairs on screen looking current.
            if self.video_folder_selector.path_or_none() != video_folder_str:
                return
            if self.subtitle_folder_selector.path_or_none() != sub_folder_str:
                return
            self.pair_preview.clear()
            self.pair_preview.hide()
            self.pair_preview_label.hide()

        run_off_thread(self, _scan, _apply, _on_error)

    def _create_output_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Output")))

        # Output location row
        out_row = QHBoxLayout()
        out_row.setSpacing(SPACING.xs)
        out_label = QLabel(self.tr("Output:"))
        out_row.addWidget(out_label)

        self.output_location_label = QLabel(self._strings.output_default)
        self.output_location_label.setObjectName("output-location-value")
        out_row.addWidget(self.output_location_label, 1)

        self.choose_output_button = ModernButton(self.tr("Choose Folder…"), variant="secondary")
        self.choose_output_button.clicked.connect(self._on_choose_output)
        out_row.addWidget(self.choose_output_button)

        self.clear_output_button = ModernButton(self.tr("Reset"), variant="secondary")
        self.clear_output_button.clicked.connect(self._on_clear_output)
        self.clear_output_button.hide()
        out_row.addWidget(self.clear_output_button)

        layout.addLayout(out_row)

        # Overwrite checkbox
        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing subtitle files"))
        self.overwrite_checkbox.setToolTip(
            self.tr("When unchecked, pairs whose output subtitle already exists are skipped, not overwritten.")
        )
        layout.addWidget(self.overwrite_checkbox)

        # Alignment tunes itself (engine chain + result validation); a result
        # that cannot be trusted never overwrites the original subtitle.
        auto_hint = QLabel(self.tr("Alignment is automatic; an untrustworthy result never replaces the original file."))
        auto_hint.setObjectName("helper-text")
        auto_hint.setWordWrap(True)
        layout.addWidget(auto_hint)

        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6).

        No Actions card any more: a card whose entire content moved to the bar
        would be a heading over nothing.
        """
        self.retime_button = ModernButton(self.tr("Retime Subtitles"), variant="primary")
        self.retime_button.clicked.connect(self._on_retime)
        # Base slots (queue-finished re-enable) act on the tool's primary button.
        self._primary_button = self.retime_button

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine / availability state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        """Probe alass availability off-thread, then update the notice.

        alass is optional now: ffsubsync ships with the app as the primary
        engine, so a missing alass shortens the fallback chain instead of
        disabling retiming. The probe only drives the informational notice.
        """
        config = self.config
        self.retime_button.setEnabled(True)
        if self._suppress_optional_startup:
            return

        def _apply(result: object) -> None:
            self._alass_is_available = bool(result)
            self.engine_notice_label.setVisible(not self._alass_is_available)

        def _on_error(message: str) -> None:
            logger.warning("alass availability probe failed: %s", message)
            _apply(False)

        self._run_availability_scan(lambda: self._compute_alass_available(config), _apply, _on_error)

    def _alass_available(self) -> bool:
        """Return the cached alass availability (probed once per config)."""
        return self._alass_is_available

    def _compute_alass_available(self, config: AnkiMinerConfig) -> bool:
        """Probe whether the alass binary is reachable for the current config.

        Runs the PATH scan (resolve_alass + shutil.which / Path.exists). Called
        only from ``__init__`` and ``update_config`` — readers use the cached
        ``self._alass_is_available`` via :meth:`_alass_available`.
        """
        resolved = resolve_alass(config)
        if resolved == "alass":
            # PATH fallback — check shutil.which
            return shutil.which("alass") is not None
        # Explicit path (config override or bundled)
        return Path(resolved).exists()

    # ------------------------------------------------------------------
    # Mode toggle slots
    # ------------------------------------------------------------------

    def _on_file_mode(self) -> None:
        self.file_mode_button.setChecked(True)
        self.folder_mode_button.setChecked(False)
        self.video_file_selector.show()
        self.subtitle_file_selector.show()
        self.track_row_widget.show()
        self.video_folder_selector.hide()
        self.subtitle_folder_selector.hide()
        self.pair_preview.hide()
        self.pair_preview_label.hide()

    def set_single_inputs(self, video_path: Path, subtitle_path: Path) -> None:
        """Prefill single-file mode with an exact pair (D35 hand-off).

        Called when the subtitle timing viewer's "Align automatically" closes:
        the user already chose both files there, so nothing is re-derived and
        nothing runs — the tool is simply put in front of them, loaded, with
        Retime left for them to press.

        Args:
            video_path: The video the subtitle should be matched against.
            subtitle_path: The subtitle file to retime.
        """
        self._on_file_mode()
        # set_path goes through the field, so path_changed fires and the
        # per-run reference pick is dropped with the video it belonged to.
        self.video_file_selector.set_path(str(video_path))
        self.subtitle_file_selector.set_path(str(subtitle_path))

    def _on_folder_mode(self) -> None:
        self.folder_mode_button.setChecked(True)
        self.file_mode_button.setChecked(False)
        self.video_file_selector.hide()
        self.subtitle_file_selector.hide()
        # Folder mode resolves the reference per video; no per-file pick.
        self.track_row_widget.hide()
        self.video_folder_selector.show()
        self.subtitle_folder_selector.show()
        self._refresh_pair_preview()

    # ------------------------------------------------------------------
    # Reference selection (single-file mode)
    # ------------------------------------------------------------------

    def _auto_reference_text(self) -> str:
        """Label for the default, un-overridden reference."""
        return self.tr("Auto - embedded subtitles, or audio")

    def _on_video_path_changed(self, new_path: str) -> None:
        """Reset the reference override when the video file changes."""
        self._reference_override = None
        self.reference_label.setText(self._auto_reference_text())

    def _on_tracks_clicked(self) -> None:
        """Open RetimeReferenceDialog to pick what alass aligns against."""
        # Not a fresh attempt (D24): opening the picker must not clear a real
        # run failure still on screen.
        video_path = self.video_file_selector.path_or_none()
        if video_path is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a video file first.")))
            return
        video_file = Path(video_path)
        if not video_file.is_file():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That video file no longer exists."), details=video_path)
            )
            return

        config = self.config

        # Probe off the GUI thread — ffprobe on a large file can block long
        # enough to freeze the UI. Disable the button so a second click can't
        # spawn a parallel probe; re-enabled in both callbacks.
        self.tracks_button.setEnabled(False)

        def _probe() -> object:
            # Subtitle streams come back in reference-preference order, so the
            # list the user reads matches the order auto-selection would try.
            return build_reference_choices(
                list_reference_subtitle_streams(config, video_file),
                list_audio_streams(video_file, ffprobe_cmd=resolve_ffprobe(config)),
            )

        def _on_choices(result: object) -> None:
            try:
                self.tracks_button.setEnabled(True)
            except RuntimeError:
                # Tab torn down while the probe was in flight (its C++ button is
                # gone); the queued callback has nothing live to update.
                return
            if self.video_file_selector.path_or_none() != video_path:
                return
            choices = cast("list[ReferenceChoice]", result)
            if not choices:
                QMessageBox.information(
                    self,
                    self.tr("No Tracks"),
                    self.tr("No audio or subtitle tracks detected. Check that ffprobe is installed."),
                )
                return

            dialog = RetimeReferenceDialog(
                streams=choices,
                current_override=self._position_of(choices, self._reference_override),
                auto_detected=None,
                parent=self,
            )
            if dialog.exec() != RetimeReferenceDialog.DialogCode.Accepted:
                return
            if self.video_file_selector.path_or_none() != video_path:
                return

            position = dialog.selected_override()
            if position is None:
                self._reference_override = None
                self.reference_label.setText(self._auto_reference_text())
                return
            picked = choices[position]
            self._reference_override = picked.to_override()
            self.reference_label.setText(
                tr_format(
                    self.tr("Subtitle track %1") if picked.kind == "subtitle" else self.tr("Audio track %1"),
                    str(picked.stream_index + 1),
                )
            )

        def _on_probe_error(msg: str) -> None:
            logger.error("Failed to probe reference tracks: %s", msg)
            try:
                self.tracks_button.setEnabled(True)
            except RuntimeError:
                # Tab torn down while the probe was in flight; nothing to surface.
                return
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Tracks could not be read."),
                    details=msg,
                    action_id="settings.media",
                    action_text=self.tr("Open Media Settings"),
                ),
                action=lambda: reveal_settings(self, "media"),
            )

        run_off_thread(self, _probe, _on_choices, _on_probe_error)

    @staticmethod
    def _position_of(choices: list[ReferenceChoice], override: ReferenceOverride | None) -> int | None:
        """Return the row *override* corresponds to, or None for Auto.

        The picker round-trips a row position, but the override we hold names a
        stream, so a re-opened dialog has to map back. A stale override (the
        track vanished) yields None, which the picker preselects as Auto.
        """
        if override is None:
            return None
        return next(
            (c.position for c in choices if c.kind == override.kind and c.stream_index == override.index),
            None,
        )

    # ------------------------------------------------------------------
    # Retime
    # ------------------------------------------------------------------

    def _on_retime(self) -> None:
        """Validate then start the SubtitleRetimeWorker."""
        # Reentrancy guard: a prior run's QThread may still be tearing down when
        # queue_finished re-enabled the button. Never reassign self.worker_thread
        # over a live thread.
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        # A fresh attempt supersedes the complaint about the last one -- both a
        # refusal ("Choose a video file first") and a problem the previous run
        # logged through `_ToolTabBase._on_log_problem`, which nothing else ever
        # cleared. After the reentrancy guard, before anything that re-raises.
        self.clear_screen_issue()

        # Clear the log before collecting: pair collection logs the pairing
        # summary ("Matched N of M") we must not wipe afterwards.
        self.log_widget.clear_log()
        self.progress_widget.reset()

        if not self.video_file_selector.isHidden():
            # Single-file mode: no directory scan, stays synchronous.
            pairs = self._collect_single_pair()
            if not pairs:
                return
            self._start_retime_worker(pairs)
            return

        # Folder mode: episode-number pairing scans two directories, which can
        # stall on a network share — run it off the GUI thread and start the
        # worker from its completion callback. Disabled here (not just at
        # worker-start) so a second click during the scan can't fire a second
        # concurrent scan.
        self.retime_button.setEnabled(False)

        def _on_pairs(pairs: list[tuple[Path, Path]]) -> None:
            if not pairs:
                self.retime_button.setEnabled(True)
                return
            self._start_retime_worker(pairs)

        self._collect_folder_pairs_async(_on_pairs)

    def _start_retime_worker(self, pairs: list[tuple[Path, Path]]) -> None:
        """Resolve the output dir, check it's writable, then build+start the worker."""
        # Resolve output directory
        if self._custom_output_dir is not None:
            out_dir: Path | None = self._custom_output_dir
        else:
            out_dir = None

        # Pre-run writable check. When out_dir is None every output lands
        # next to its source video, so check the first video's parent.
        check_dir = out_dir if out_dir is not None else pairs[0][0].parent
        if not os.access(check_dir, os.W_OK):
            self.log_widget.append_error(self.tr("Output directory is not writable: ") + str(check_dir))
            self.retime_button.setEnabled(True)
            return

        # Build and start worker
        self._begin_tool_run(len(pairs))
        self._total_pairs = len(pairs)

        # Single-file mode honors the per-file reference pick; a folder's videos
        # each get their own auto-resolution, so one override cannot apply.
        reference_override = self._reference_override if not self.video_file_selector.isHidden() else None

        worker = SubtitleRetimeWorker(
            self.config,
            pairs,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
            reference_override=reference_override,
        )
        self.worker_thread = worker

        # Wire signals
        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_note.connect(self._on_file_note)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        worker.error.connect(self._on_run_error)
        # Lifecycle: free the QThread on real thread exit (not on queue_finished,
        # which fires just before the thread ends). Clears the handle so the
        # reentrancy guard and iter_close_workers see no stale worker.
        worker.finished.connect(self._on_worker_finished)

        self.retime_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    def _collect_single_pair(self) -> list[tuple[Path, Path]]:
        """Single-file mode: return [(video, subtitle)], or [] on failure."""
        video_str = self.video_file_selector.path_or_none()
        sub_str = self.subtitle_file_selector.path_or_none()

        if video_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a video file before retiming subtitles.")))
            return []
        if sub_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a subtitle file before retiming subtitles.")))
            return []

        video = Path(video_str)
        sub = Path(sub_str)

        if not video.is_file():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That video file no longer exists."), details=video_str))
            return []
        if not sub.is_file():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That subtitle file no longer exists."), details=sub_str)
            )
            return []

        return [(video, sub)]

    def _collect_folder_pairs_async(self, on_pairs: Callable[[list[tuple[Path, Path]]], None]) -> None:
        """Folder mode: resolve+scan both folders off the GUI thread, then call
        ``on_pairs`` on the GUI thread with the matched (video, subtitle) pairs
        (``[]`` when collection fails — screen-issue/log feedback already shown).

        The two directory listings (episode-number pairing plus the raw video
        count for the log message) can stall on a network share, so only the
        cheap ``is_dir()`` validation above runs synchronously; the scan itself
        is dispatched via :func:`run_off_thread`.
        """
        video_folder_str = self.video_folder_selector.path_or_none()
        sub_folder_str = self.subtitle_folder_selector.path_or_none()

        if video_folder_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a video folder before retiming subtitles.")))
            on_pairs([])
            return
        if sub_folder_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a subtitle folder before retiming subtitles.")))
            on_pairs([])
            return

        video_folder = Path(video_folder_str)
        sub_folder = Path(sub_folder_str)

        if not video_folder.is_dir():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That video folder no longer exists."), details=video_folder_str)
            )
            on_pairs([])
            return
        if not sub_folder.is_dir():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That subtitle folder no longer exists."), details=sub_folder_str)
            )
            on_pairs([])
            return

        def _scan() -> object:
            all_videos = sorted(
                f
                for f in video_folder.iterdir()
                if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
            )
            # Same pairing as the preview above, including prefer_retimed=False.
            file_pairs = FilePairMatcher.find_pairs_by_episode_number(video_folder, sub_folder, prefer_retimed=False)
            return all_videos, file_pairs

        def _apply(result: object) -> None:
            all_videos, file_pairs = cast("tuple[list[Path], list[FilePair]]", result)
            total_videos = len(all_videos)
            n_matched = len(file_pairs)

            self.log_widget.append_success(
                tr_format(self.tr("Matched %1 of %2 video files."), str(n_matched), str(total_videos))
            )

            if n_matched < total_videos:
                matched_videos = {fp.video for fp in file_pairs}
                unmatched = [v for v in all_videos if v not in matched_videos]
                n_unmatched = len(unmatched)
                self.log_widget.append_error(
                    tr_format(self.tr("Warning: %1 video file(s) could not be matched."), str(n_unmatched))
                )

            if not file_pairs:
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr("No subtitle file could be matched to any video file in those folders.")
                    )
                )
                on_pairs([])
                return

            on_pairs([(fp.video, fp.subtitle) for fp in file_pairs])

        def _on_error(_msg: str) -> None:
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That video folder could not be read."), details=video_folder_str)
            )
            on_pairs([])

        run_off_thread(self, _scan, _apply, _on_error)

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Retiming file %1 of %2"), str(idx + 1), str(self._total_pairs))
        )

    def _on_file_note(self, idx: int, note: str) -> None:
        """Durable per-file detail (C-7/C-10): the engine that won. Unlike
        ``file_progress``, this always lands in the Activity log, so it survives
        past the moment the next status update overwrites the transient label.
        """
        self.log_widget.append_info(note)

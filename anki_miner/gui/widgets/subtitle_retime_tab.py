"""Subtitle Retime tab — retime subtitle files to video using alass.

Composes four :class:`~anki_miner.gui.widgets.enhanced.FileSelector` instances
(single-file / folder mode toggle — video + subtitle selectors per mode), an
output-location row, an Overwrite checkbox, a Split penalty spinbox, a Retime
button, a :class:`~anki_miner.gui.widgets.progress_widget.ProgressWidget` for
overall queue progress, and a :class:`~anki_miner.gui.widgets.log_widget.LogWidget`
for per-pair pass/fail lines.

Guard contract:
- alass not found → Retime disabled, notice visible.
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
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.constants import SUBTITLE_FILE_FILTER, VIDEO_FILE_FILTER
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.dialogs import AudioTracksDialog
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.subtitle_retime_worker import SubtitleRetimeWorker
from anki_miner.utils import list_audio_streams
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.utils.audio_track_detector import AudioStream

logger = logging.getLogger(__name__)

_SPLIT_PENALTY_DEFAULT = 7.0
_SPLIT_PENALTY_MIN = 0.0
_SPLIT_PENALTY_MAX = 1000.0
_SPLIT_PENALTY_STEP = 1.0


class SubtitleRetimeTab(QWidget):
    """Tab for retiming subtitle files to video using alass.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.worker_thread: SubtitleRetimeWorker | None = None
        self._custom_output_dir: Path | None = None
        self._total_pairs: int = 0
        self._cancelled: bool = False
        # Per-run audio-track selection for single-file mode (audio-stream index,
        # or None for Japanese auto-detect). Reset when the video changes.
        self._audio_track_override: int | None = None
        # alass availability is cached per-config: probing it (resolve_alass +
        # shutil.which / Path.exists) is a PATH scan we must not repeat on every
        # _alass_available() read. Recomputed only here and in update_config().
        self._alass_is_available: bool = self._compute_alass_available()

        self._setup_ui()
        self._refresh_engine_state()

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
        self._alass_is_available = self._compute_alass_available()
        self._refresh_engine_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_input_section())
        layout.addWidget(self._create_output_section())
        layout.addWidget(self._create_actions_section())
        layout.addWidget(self._create_progress_section())
        layout.addStretch()

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    def _create_input_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Input")))

        # alass notice (shown when alass unavailable)
        self.engine_notice_label = QLabel(
            self.tr("alass not found; install it or set its path in Settings to enable retiming.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        # Input description
        input_desc = QLabel(
            self.tr("Resync a subtitle file to its video by matching audio. Pick a video and the subtitle to align.")
        )
        input_desc.setObjectName("helper-text")
        input_desc.setWordWrap(True)
        layout.addWidget(input_desc)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_label = QLabel(self.tr("Mode:"))
        mode_row.addWidget(mode_label)

        self.file_mode_button = ModernButton(self.tr("Single File"), variant="primary")
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
        )
        layout.addWidget(self.video_file_selector)

        self.subtitle_file_selector = FileSelector(
            label=self.tr("Subtitle File:"),
            file_mode=True,
            file_filter=SUBTITLE_FILE_FILTER,
        )
        layout.addWidget(self.subtitle_file_selector)

        # Reset the audio-track override whenever the video changes (selection is
        # per-run and must not silently carry over to a different file).
        self.video_file_selector.path_changed.connect(self._on_video_path_changed)

        # Single-mode audio-track override row. alass has no track flag, so the
        # chosen track is pre-extracted and handed to alass as the reference.
        self.track_row_widget = QWidget()
        track_row = QHBoxLayout(self.track_row_widget)
        track_row.setContentsMargins(0, 0, 0, 0)
        track_row.setSpacing(SPACING.xs)
        track_row.addWidget(QLabel(self.tr("Audio track:")))
        self.audio_track_label = QLabel(self.tr("Japanese (auto-detect)"))
        self.audio_track_label.setObjectName("output-location-value")
        track_row.addWidget(self.audio_track_label, 1)
        self.tracks_button = ModernButton(self.tr("Tracks…"), variant="secondary")
        self.tracks_button.setToolTip(self.tr("Choose which audio track to align the subtitle against."))
        self.tracks_button.clicked.connect(self._on_tracks_clicked)
        track_row.addWidget(self.tracks_button)
        layout.addWidget(self.track_row_widget)

        # Folder-mode selectors (hidden by default)
        self.video_folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
        )
        self.video_folder_selector.hide()
        layout.addWidget(self.video_folder_selector)

        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
        )
        self.subtitle_folder_selector.hide()
        layout.addWidget(self.subtitle_folder_selector)

        group.setLayout(layout)
        return group

    def _create_output_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Output")))

        # Output location row
        out_row = QHBoxLayout()
        out_row.setSpacing(SPACING.xs)
        out_label = QLabel(self.tr("Output:"))
        out_row.addWidget(out_label)

        self.output_location_label = QLabel(self.tr("Next to source video"))
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

        # Frame-rate correction toggle. Default OFF: resyncing a sub to its own
        # video needs no framerate change, and alass's FPS guessing otherwise
        # stretches an already-good sub and breaks the timing. Enable only for a
        # sub sourced from a different-framerate release.
        self.fps_correction_checkbox = QCheckBox(self.tr("Correct frame-rate differences"))
        self.fps_correction_checkbox.setChecked(False)
        self.fps_correction_checkbox.setToolTip(
            self.tr(
                "Leave off when the subtitle already matches this video's framerate. "
                "Only enable for subs from a different release/framerate."
            )
        )
        layout.addWidget(self.fps_correction_checkbox)

        # Single-offset toggle: shift the whole subtitle by one constant amount
        # instead of cutting it into independently-aligned segments.
        self.no_split_checkbox = QCheckBox(self.tr("Single offset only (no split)"))
        self.no_split_checkbox.setChecked(False)
        self.no_split_checkbox.setToolTip(
            self.tr("Shift the entire subtitle by one offset; never cut it into separately-timed segments.")
        )
        layout.addWidget(self.no_split_checkbox)

        # Split penalty row
        penalty_row = QHBoxLayout()
        penalty_row.setSpacing(SPACING.xs)
        penalty_label = QLabel(self.tr("Split penalty:"))
        penalty_row.addWidget(penalty_label)

        self.split_penalty_spinbox = QDoubleSpinBox()
        self.split_penalty_spinbox.setRange(_SPLIT_PENALTY_MIN, _SPLIT_PENALTY_MAX)
        self.split_penalty_spinbox.setValue(_SPLIT_PENALTY_DEFAULT)
        self.split_penalty_spinbox.setSingleStep(_SPLIT_PENALTY_STEP)
        self.split_penalty_spinbox.setToolTip(
            self.tr("Lower = more cut points for ad breaks; " "1–20 is the useful range; default 7")
        )
        penalty_row.addWidget(self.split_penalty_spinbox)
        penalty_row.addStretch()
        layout.addLayout(penalty_row)

        # Split penalty inline explanation
        self.split_penalty_helper = QLabel(
            self.tr("Lower values create more cut points for ad breaks. Useful range 1–20; default 7.")
        )
        self.split_penalty_helper.setObjectName("helper-text")
        self.split_penalty_helper.setWordWrap(True)
        layout.addWidget(self.split_penalty_helper)

        group.setLayout(layout)
        return group

    def _create_actions_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Actions")))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACING.xs)

        self.retime_button = ModernButton(self.tr("Retime Subtitles"), variant="primary")
        self.retime_button.clicked.connect(self._on_retime)
        btn_row.addWidget(self.retime_button)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="danger")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()
        btn_row.addWidget(self.cancel_button)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        group.setLayout(layout)
        return group

    def _create_progress_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Progress")))

        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    # Engine / availability state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        """Enable/disable Retime based on alass availability."""
        available = self._alass_available()
        self.engine_notice_label.setVisible(not available)
        self.retime_button.setEnabled(available)

    def _alass_available(self) -> bool:
        """Return the cached alass availability (probed once per config)."""
        return self._alass_is_available

    def _compute_alass_available(self) -> bool:
        """Probe whether the alass binary is reachable for the current config.

        Runs the PATH scan (resolve_alass + shutil.which / Path.exists). Called
        only from ``__init__`` and ``update_config`` — readers use the cached
        ``self._alass_is_available`` via :meth:`_alass_available`.
        """
        resolved = resolve_alass(self.config)
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

    def _on_folder_mode(self) -> None:
        self.folder_mode_button.setChecked(True)
        self.file_mode_button.setChecked(False)
        self.video_file_selector.hide()
        self.subtitle_file_selector.hide()
        # Folder mode auto-detects the Japanese track per video; no per-file pick.
        self.track_row_widget.hide()
        self.video_folder_selector.show()
        self.subtitle_folder_selector.show()

    # ------------------------------------------------------------------
    # Audio track selection (single-file mode)
    # ------------------------------------------------------------------

    def _on_video_path_changed(self, new_path: str) -> None:
        """Reset the audio-track override when the video file changes."""
        self._audio_track_override = None
        self.audio_track_label.setText(self.tr("Japanese (auto-detect)"))

    def _on_tracks_clicked(self) -> None:
        """Open AudioTracksDialog to pick which audio track alass aligns against."""
        video_path = self.video_file_selector.get_path().strip()
        if not video_path:
            QMessageBox.warning(self, self.tr("No Video File Selected"), self.tr("Select a video file first."))
            return
        video_file = Path(video_path)
        if not video_file.is_file():
            QMessageBox.warning(self, self.tr("File Not Found"), self.tr("Video file not found: ") + video_path)
            return

        ffprobe_cmd = resolve_ffprobe(self.config)

        # Probe off the GUI thread — ffprobe on a large file can block long
        # enough to freeze the UI. Disable the button so a second click can't
        # spawn a parallel probe; re-enabled in both callbacks.
        self.tracks_button.setEnabled(False)

        def _probe() -> object:
            return list_audio_streams(video_file, ffprobe_cmd=ffprobe_cmd)

        def _on_streams(result: object) -> None:
            self.tracks_button.setEnabled(True)
            streams = cast("list[AudioStream]", result)
            if not streams:
                QMessageBox.information(
                    self,
                    self.tr("No Audio Tracks"),
                    self.tr("No audio tracks detected. Check that ffprobe is installed and the file has audio."),
                )
                return

            auto_stream = next((s for s in streams if s.language_tag in JAPANESE_LANGUAGE_CODES), None)

            dialog = AudioTracksDialog(
                streams=streams,
                current_override=self._audio_track_override,
                auto_detected=auto_stream,
                parent=self,
            )
            if dialog.exec() == AudioTracksDialog.DialogCode.Accepted:
                self._audio_track_override = dialog.selected_override()
                if self._audio_track_override is None:
                    self.audio_track_label.setText(self.tr("Japanese (auto-detect)"))
                else:
                    self.audio_track_label.setText(tr_format(self.tr("Track %1"), str(self._audio_track_override + 1)))

        def _on_probe_error(msg: str) -> None:
            self.tracks_button.setEnabled(True)
            logger.error("Failed to probe audio tracks: %s", msg)
            QMessageBox.warning(
                self,
                self.tr("Probe Failed"),
                self.tr("Failed to detect audio tracks. Check that ffprobe is installed."),
            )

        run_off_thread(self, _probe, _on_streams, _on_probe_error)

    # ------------------------------------------------------------------
    # Output location slots
    # ------------------------------------------------------------------

    def _on_choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self.tr("Select Output Folder"),
            str(Path.home()),
        )
        if folder:
            self._custom_output_dir = Path(folder)
            self.output_location_label.setText(folder)
            self.clear_output_button.show()

    def _on_clear_output(self) -> None:
        self._custom_output_dir = None
        self.output_location_label.setText(self.tr("Next to source video"))
        self.clear_output_button.hide()

    # ------------------------------------------------------------------
    # Retime
    # ------------------------------------------------------------------

    def _on_retime(self) -> None:
        """Validate then start the SubtitleRetimeWorker."""
        if not self._alass_available():
            # Should not happen (button disabled), but guard anyway.
            return

        # Reentrancy guard: a prior run's QThread may still be tearing down when
        # queue_finished re-enabled the button. Never reassign self.worker_thread
        # over a live thread.
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        # Collect pairs
        pairs = self._collect_pairs()
        if not pairs:
            return

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
            return

        # Build and start worker
        self._cancelled = False
        self._total_pairs = len(pairs)
        self.log_widget.clear_log()
        self.progress_widget.reset()
        self.progress_widget.set_determinate(self._total_pairs)

        # Single-file mode honors the per-file track pick; folder mode auto-detects.
        track_override = self._audio_track_override if not self.video_file_selector.isHidden() else None

        worker = SubtitleRetimeWorker(
            self.config,
            pairs,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
            split_penalty=self.split_penalty_spinbox.value(),
            disable_fps_guessing=not self.fps_correction_checkbox.isChecked(),
            no_split=self.no_split_checkbox.isChecked(),
            audio_track_override=track_override,
        )
        self.worker_thread = worker

        # Wire signals
        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        # Lifecycle: free the QThread on real thread exit (not on queue_finished,
        # which fires just before the thread ends). Clears the handle so the
        # reentrancy guard and iter_close_workers see no stale worker.
        worker.finished.connect(self._on_worker_finished)

        self.retime_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    def _collect_pairs(self) -> list[tuple[Path, Path]]:
        """Return the ordered list of (video, subtitle) pairs to process, or [] on failure."""
        if not self.video_file_selector.isHidden():
            # Single-file mode
            video_str = self.video_file_selector.get_path().strip()
            sub_str = self.subtitle_file_selector.get_path().strip()

            if not video_str:
                QMessageBox.warning(
                    self,
                    self.tr("No Video File Selected"),
                    self.tr("Select a video file before retiming subtitles."),
                )
                return []
            if not sub_str:
                QMessageBox.warning(
                    self,
                    self.tr("No Subtitle File Selected"),
                    self.tr("Select a subtitle file before retiming subtitles."),
                )
                return []

            video = Path(video_str)
            sub = Path(sub_str)

            if not video.is_file():
                QMessageBox.warning(
                    self,
                    self.tr("File Not Found"),
                    self.tr("Video file not found: ") + video_str,
                )
                return []
            if not sub.is_file():
                QMessageBox.warning(
                    self,
                    self.tr("File Not Found"),
                    self.tr("Subtitle file not found: ") + sub_str,
                )
                return []

            return [(video, sub)]

        else:
            # Folder mode
            video_folder_str = self.video_folder_selector.get_path().strip()
            sub_folder_str = self.subtitle_folder_selector.get_path().strip()

            if not video_folder_str:
                QMessageBox.warning(
                    self,
                    self.tr("No Video Folder Selected"),
                    self.tr("Select a video folder before retiming subtitles."),
                )
                return []
            if not sub_folder_str:
                QMessageBox.warning(
                    self,
                    self.tr("No Subtitle Folder Selected"),
                    self.tr("Select a subtitle folder before retiming subtitles."),
                )
                return []

            video_folder = Path(video_folder_str)
            sub_folder = Path(sub_folder_str)

            if not video_folder.is_dir():
                QMessageBox.warning(
                    self,
                    self.tr("Folder Not Found"),
                    self.tr("Video folder not found: ") + video_folder_str,
                )
                return []
            if not sub_folder.is_dir():
                QMessageBox.warning(
                    self,
                    self.tr("Folder Not Found"),
                    self.tr("Subtitle folder not found: ") + sub_folder_str,
                )
                return []

            # Count total video files for the log message
            all_videos = sorted(
                f
                for f in video_folder.iterdir()
                if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
            )
            total_videos = len(all_videos)

            file_pairs = FilePairMatcher.find_pairs_by_episode_number(video_folder, sub_folder)
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
                QMessageBox.warning(
                    self,
                    self.tr("No Pairs Matched"),
                    self.tr("No subtitle files could be matched to the video files in the selected folders."),
                )
                return []

            return [(fp.video, fp.subtitle) for fp in file_pairs]

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Retiming file %1 of %2"), str(idx + 1), str(self._total_pairs))
        )

    def _on_file_progress(self, idx: int, pct: int, message: str) -> None:
        self.progress_widget.set_status(message)

    def _on_file_finished(self, idx: int, out_path: object, error_str: object) -> None:
        # Advance the progress bar to reflect completed pairs.
        self.progress_widget.set_progress(idx + 1, self._total_pairs)
        if error_str:
            self.log_widget.append_error(str(error_str))
        else:
            path_label = str(out_path) if out_path else ""
            self.log_widget.append_success(self.tr("Done: ") + Path(path_label).name if path_label else self.tr("Done"))

    def _on_file_skipped(self, idx: int, out_path: object) -> None:
        # Advance the progress bar just like a finished pair.
        self.progress_widget.set_progress(idx + 1, self._total_pairs)
        path_label = str(out_path) if out_path else ""
        self.log_widget.append_info(self.tr("Skipped: ") + Path(path_label).name if path_label else self.tr("Skipped"))

    def _on_queue_finished(self) -> None:
        self.retime_button.setEnabled(True)
        self.cancel_button.hide()
        # Reset for the next run's cancel button.
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        self.progress_widget.set_status(self.tr("Cancelled") if self._cancelled else self.tr("Finished"))

    def _on_worker_finished(self) -> None:
        """Release the QThread once it has actually exited."""
        worker = self.worker_thread
        if worker is not None:
            worker.deleteLater()
            self.worker_thread = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        self._cancelled = True
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling…"))
        self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[SubtitleRetimeWorker]:
        """Yield the active worker so BackgroundTaskController can join it on close."""
        if self.worker_thread is not None:
            yield self.worker_thread

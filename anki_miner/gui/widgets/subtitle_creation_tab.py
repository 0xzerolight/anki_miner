"""Subtitle Creation tab — transcribe video files to SRT using the ASR engine.

Composes two :class:`~anki_miner.gui.widgets.enhanced.FileSelector` instances
(single-file / folder mode toggle), an output-location row, an Overwrite
checkbox, a Generate button, a :class:`~anki_miner.gui.widgets.progress_widget.ProgressWidget`
for overall queue progress, and a :class:`~anki_miner.gui.widgets.log_widget.LogWidget`
for per-file pass/fail lines.

Guard contract:
- ``_engine.available()`` False → Generate disabled, notice visible.
- Model not downloaded → Generate shows a prompt directing the user to Settings.
- Output directory not writable → Generate aborts, error logged.

Worker contract:
- Worker stored on ``self.worker_thread``.
- ``iter_close_workers()`` yields the active worker for
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
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
from anki_miner.gui.constants import VIDEO_FILE_FILTER
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.subtitle_gen_worker import SubtitleGenWorker
from anki_miner.services.asr import _engine, model_manager
from anki_miner.utils.file_pairing import FilePairMatcher

logger = logging.getLogger(__name__)


class SubtitleCreationTab(QWidget):
    """Tab for generating SRT subtitle files from video files via ASR.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.worker_thread: SubtitleGenWorker | None = None
        self._custom_output_dir: Path | None = None

        self._setup_ui()
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

        # Read-only language label
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(self.tr("Language:")))
        self.language_label = QLabel(self.tr("Japanese"))
        lang_row.addWidget(self.language_label)
        lang_row.addStretch()
        layout.addLayout(lang_row)

        # Engine notice (shown when engine unavailable)
        self.engine_notice_label = QLabel(
            self.tr(
                "ASR engine not available. "
                "Install the [asr] extra (faster-whisper + ctranslate2) and "
                "download a model in Settings → ASR to enable subtitle generation."
            )
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_label = QLabel(self.tr("Mode:"))
        mode_row.addWidget(mode_label)

        self.file_mode_button = ModernButton(self.tr("Single File"), variant="primary")
        self.file_mode_button.setCheckable(True)
        self.file_mode_button.setChecked(True)
        self.file_mode_button.clicked.connect(self._on_file_mode)
        mode_row.addWidget(self.file_mode_button)

        self.folder_mode_button = ModernButton(self.tr("Folder"), variant="secondary")
        self.folder_mode_button.setCheckable(True)
        self.folder_mode_button.setChecked(False)
        self.folder_mode_button.clicked.connect(self._on_folder_mode)
        mode_row.addWidget(self.folder_mode_button)

        mode_row.addStretch()
        layout.addLayout(mode_row)

        # File selector (single-file mode)
        self.file_selector = FileSelector(
            label=self.tr("Video File:"),
            file_mode=True,
            file_filter=VIDEO_FILE_FILTER,
        )
        layout.addWidget(self.file_selector)

        # Folder selector (folder mode, hidden by default)
        self.folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
        )
        self.folder_selector.hide()
        layout.addWidget(self.folder_selector)

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
        self.output_location_label.setObjectName("helper-text")
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
        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing SRT files"))
        layout.addWidget(self.overwrite_checkbox)

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

        self.generate_button = ModernButton(self.tr("Generate Subtitles"), variant="primary")
        self.generate_button.clicked.connect(self._on_generate)
        btn_row.addWidget(self.generate_button)

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
    # Engine / model state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        """Enable/disable Generate based on engine availability."""
        available = _engine.available()
        self.engine_notice_label.setVisible(not available)
        self.generate_button.setEnabled(available)

    # ------------------------------------------------------------------
    # Mode toggle slots
    # ------------------------------------------------------------------

    def _on_file_mode(self) -> None:
        self.file_mode_button.setChecked(True)
        self.folder_mode_button.setChecked(False)
        self.file_selector.show()
        self.folder_selector.hide()

    def _on_folder_mode(self) -> None:
        self.folder_mode_button.setChecked(True)
        self.file_mode_button.setChecked(False)
        self.file_selector.hide()
        self.folder_selector.show()

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
    # Generate
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        """Validate then start the SubtitleGenWorker."""
        if not _engine.available():
            # Should not happen (button disabled), but guard anyway.
            return

        # Collect video file list
        video_files = self._collect_video_files()
        if not video_files:
            return

        # Resolve output directory
        if self._custom_output_dir is not None:
            out_dir: Path | None = self._custom_output_dir
        else:
            # "Next to source video" — each file goes next to itself.
            # Pass None to the worker so it uses video_path.with_suffix(".srt").
            out_dir = None

        # Pre-run writable check.  When out_dir is None every output lands
        # next to its source video, so check the first source file's parent.
        check_dir = out_dir if out_dir is not None else video_files[0].parent
        if not os.access(check_dir, os.W_OK):
            self.log_widget.append_error(self.tr("Output directory is not writable: ") + str(check_dir))
            return

        # Model-downloaded guard
        if not model_manager.is_downloaded(self.config.asr_model, self.config.asr_models_root):
            QMessageBox.warning(
                self,
                self.tr("Model Not Downloaded"),
                self.tr(
                    "The selected ASR model (%1) has not been downloaded yet.\n"
                    "Go to Settings → ASR to download it before generating subtitles."
                ).replace("%1", self.config.asr_model),
            )
            return

        # Build and start worker
        self.log_widget.clear_log()
        self.progress_widget.reset()
        self.progress_widget.set_determinate(len(video_files))

        worker = SubtitleGenWorker(
            self.config,
            video_files,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
        )
        self.worker_thread = worker

        # Wire signals
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.queue_finished.connect(self._on_queue_finished)

        self.generate_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    def _collect_video_files(self) -> list[Path]:
        """Return the ordered list of video files to process, or [] on validation failure."""
        if not self.file_selector.isHidden():
            path_str = self.file_selector.get_path().strip()
            if not path_str:
                QMessageBox.warning(
                    self,
                    self.tr("No File Selected"),
                    self.tr("Select a video file before generating subtitles."),
                )
                return []
            p = Path(path_str)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    self.tr("File Not Found"),
                    self.tr("Video file not found: ") + path_str,
                )
                return []
            return [p]
        else:
            path_str = self.folder_selector.get_path().strip()
            if not path_str:
                QMessageBox.warning(
                    self,
                    self.tr("No Folder Selected"),
                    self.tr("Select a folder before generating subtitles."),
                )
                return []
            folder = Path(path_str)
            if not folder.is_dir():
                QMessageBox.warning(
                    self,
                    self.tr("Folder Not Found"),
                    self.tr("Folder not found: ") + path_str,
                )
                return []
            files = sorted(
                f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
            )
            if not files:
                QMessageBox.warning(
                    self,
                    self.tr("No Video Files"),
                    self.tr("No video files found in the selected folder."),
                )
                return []
            return files

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_progress(self, idx: int, pct: int, message: str) -> None:
        self.progress_widget.set_status(message)

    def _on_file_finished(self, idx: int, out_path: object, error_str: object) -> None:
        if error_str:
            self.log_widget.append_error(str(error_str))
        else:
            path_label = str(out_path) if out_path else ""
            self.log_widget.append_success(self.tr("Done: ") + Path(path_label).name if path_label else self.tr("Done"))

    def _on_queue_finished(self) -> None:
        self.generate_button.setEnabled(True)
        self.cancel_button.hide()
        self.progress_widget.set_status(self.tr("Finished"))

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self.tr("Cancelling…"))
        self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[SubtitleGenWorker]:
        """Yield the active worker so BackgroundTaskController can join it on close."""
        if self.worker_thread is not None:
            yield self.worker_thread

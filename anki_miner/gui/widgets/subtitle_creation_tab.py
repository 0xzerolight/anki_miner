"""Subtitle Creation tab — transcribe video/audio files to SRT using ASR.

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

from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import reveal_settings
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.workers.subtitle_gen_worker import SubtitleGenWorker
from anki_miner.services.asr import _engine, ggml_model_installer, model_manager
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".m4a", ".m4b", ".aac", ".flac", ".opus", ".ogg", ".wav"})
_MEDIA_EXTENSIONS: frozenset[str] = FilePairMatcher.VIDEO_EXTENSIONS | _AUDIO_EXTENSIONS
_MEDIA_FILE_FILTER = (
    "Media Files (" + " ".join(f"*{extension}" for extension in sorted(_MEDIA_EXTENSIONS)) + ");;All Files (*)"
)


class SubtitleCreationTab(_ToolTabBase):
    """Tab for generating SRT subtitle files from video/audio files via ASR.

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
    TASK_ID = "tools.generate"
    TASK_OWNER = CapabilityTarget("subtitles", "generate")

    #: Where this tool last wrote — remembered separately from its inputs (D7).
    OUTPUT_HISTORY_KEY = "tools.generate.output"

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
        self._total_files: int = 0
        self._cancelled: bool = False
        self._engine_is_available: bool = False
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
            run_problem=self.tr("Some files could not be transcribed."),
            complete_template=self.tr("Complete — %1 files processed"),
            complete_skipped_template=self.tr("Complete — %1 processed, %2 skipped"),
            all_skipped_template=self.tr(
                "No subtitles generated — all %1 skipped because their output already exists. "
                "Enable Overwrite to regenerate."
            ),
            select_output_folder=self.tr("Select Output Folder"),
            output_default=self.tr("Next to source media"),
            task_title=self.tr("Subtitle generation"),
        )

        self._setup_ui()
        self._refresh_engine_state()

    def _item_total(self) -> int:
        return self._total_files

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new application config (e.g. after the ASR model changes).

        Wired to ``settings_tab.config_changed`` and ``window.config_refreshed``
        in ``app.main`` so a model switch in Settings is reflected by the
        model-downloaded guard and the worker. A run already in flight keeps the
        config it captured at construction.
        """
        self.config = config
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

        # Read-only language label
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(self.tr("Language:")))
        self.language_label = QLabel(self.tr("Japanese"))
        lang_row.addWidget(self.language_label)
        lang_row.addStretch()
        layout.addLayout(lang_row)

        # Engine notice (shown when engine unavailable)
        # The destination named here has to be the real one: the page this
        # sentence used to send people to does not exist (D24, string 1).
        self.engine_notice_label = QLabel(
            self.tr("Transcription is not ready. Open Settings → Transcription & Alignment to finish setup.")
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

        self.file_mode_button = ModernButton(self.tr("Single File"), variant="secondary")
        self.file_mode_button.setCheckable(True)
        self.file_mode_button.setChecked(True)
        self.file_mode_button.setToolTip(self.tr("Transcribe one selected video or audio file."))
        self.file_mode_button.clicked.connect(self._on_file_mode)
        mode_row.addWidget(self.file_mode_button)

        self.folder_mode_button = ModernButton(self.tr("Folder"), variant="secondary")
        self.folder_mode_button.setCheckable(True)
        self.folder_mode_button.setChecked(False)
        self.folder_mode_button.setToolTip(self.tr("Transcribe every video or audio file in a selected folder."))
        self.folder_mode_button.clicked.connect(self._on_folder_mode)
        mode_row.addWidget(self.folder_mode_button)

        mode_row.addStretch()
        layout.addLayout(mode_row)

        # File selector (single-file mode)
        self.file_selector = FileSelector(
            label=self.tr("Video or Audio File:"),
            file_mode=True,
            file_filter=_MEDIA_FILE_FILTER,
            history_key="tools.generate.inputs",
            drop_validator=accepts_suffixes(_MEDIA_EXTENSIONS, self.tr("This field takes a video or audio file.")),
        )
        layout.addWidget(self.file_selector)

        # Folder selector (folder mode, hidden by default)
        self.folder_selector = FileSelector(
            label=self.tr("Video or Audio Folder:"),
            file_mode=False,
            history_key="tools.generate.inputs",
        )
        self.folder_selector.hide()
        layout.addWidget(self.folder_selector)

        group.setLayout(layout)
        return group

    def _create_output_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Output")))

        # Output description
        out_desc = QLabel(
            self.tr("Generated .srt files are saved next to each source file unless you choose a folder.")
        )
        out_desc.setObjectName("helper-text")
        out_desc.setWordWrap(True)
        layout.addWidget(out_desc)

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
        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing SRT files"))
        self.overwrite_checkbox.setToolTip(
            self.tr("When unchecked, media files that already have an .srt file are skipped, not overwritten.")
        )
        layout.addWidget(self.overwrite_checkbox)

        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6).

        No Actions card any more: a card whose entire content moved to the bar
        would be a heading over nothing.
        """
        self.generate_button = ModernButton(self.tr("Generate Subtitles"), variant="primary")
        self.generate_button.clicked.connect(self._on_generate)
        # Base slots (queue-finished re-enable) act on the tool's primary button.
        self._primary_button = self.generate_button

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine / model state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        """Probe engine availability off-thread, then update the Generate guard."""
        self.generate_button.setEnabled(False)
        if self._suppress_optional_startup:
            return

        def _apply(result: object) -> None:
            self._engine_is_available = bool(result)
            self.engine_notice_label.setVisible(not self._engine_is_available)
            self.generate_button.setEnabled(self._engine_is_available)

        def _on_error(message: str) -> None:
            logger.warning("ASR availability probe failed: %s", message)
            _apply(False)

        self._run_availability_scan(_engine.available, _apply, _on_error)

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
    # Generate
    # ------------------------------------------------------------------

    def _any_usable_model_installed(self) -> bool:
        """True iff an installed model can serve the configured device route.

        CT2 layout satisfies every route. The ggml pair (acoustic + VAD)
        satisfies only devices that can route to whisper.cpp (vulkan/auto) and
        only when the backend itself is present; cpu/cuda are pure CT2. Any
        probe surprise counts as not-installed (the run would fail anyway).
        """
        if model_manager.is_downloaded(self.config.asr_model, self.config.asr_models_root):
            return True
        if self.config.asr_device not in ("vulkan", "auto"):
            return False
        try:
            return (
                _engine.whisper_cpp_available()
                and ggml_model_installer.is_ggml_downloaded(self.config.asr_model, self.config.asr_models_root)
                and ggml_model_installer.is_vad_downloaded(self.config.asr_models_root)
            )
        except Exception:  # noqa: BLE001 — bucket B: fall back to the guard message.
            return False

    def _on_generate(self) -> None:
        """Validate then start the SubtitleGenWorker."""
        if not self._engine_is_available:
            # Should not happen (button disabled), but guard anyway.
            return

        # Reentrancy guard: a prior run's QThread may still be tearing down when
        # queue_finished re-enabled the button. Never reassign self.worker_thread
        # over a live thread.
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        # A fresh attempt supersedes the complaint about the last one -- both a
        # refusal ("Choose a folder before generating subtitles") and a problem
        # the previous run logged through `_ToolTabBase._on_log_problem`, which
        # nothing else ever cleared. After the reentrancy guard, before anything
        # that re-raises.
        self.clear_screen_issue()

        # Collect media file list
        video_files = self._collect_video_files()
        if not video_files:
            return

        # Resolve output directory
        if self._custom_output_dir is not None:
            out_dir: Path | None = self._custom_output_dir
        else:
            # "Next to source media" — each file goes next to itself.
            # Pass None to the worker so it uses video_path.with_suffix(".srt").
            out_dir = None

        # Pre-run writable check.  When out_dir is None every output lands
        # next to its source media, so check the first source file's parent.
        check_dir = out_dir if out_dir is not None else video_files[0].parent
        if not os.access(check_dir, os.W_OK):
            self.log_widget.append_error(self.tr("Output directory is not writable: ") + str(check_dir))
            return

        # Model-downloaded guard — mirrors the runtime engine cascade
        # (transcriber._use_whisper_cpp_engine): a run with device vulkan/auto,
        # whisper.cpp present, and BOTH ggml files (acoustic + VAD) on disk
        # routes to whisper.cpp and never touches the CT2 layout, so a
        # CT2-only check would block a fully usable configuration. Deliberately
        # does NOT probe Vulkan devices here: vulkan_device_count() can re-exec
        # the bundle with a 15 s timeout on first call and this runs on the GUI
        # thread — if the device turns out to be absent at run time the worker
        # falls back to CT2 and surfaces the real error.
        if not self._any_usable_model_installed():
            self.show_screen_issue(
                ScreenIssue(
                    summary=tr_format(
                        self.tr(
                            "The transcription model %1 is not installed. "
                            "Open Settings → Transcription & Alignment to install it."
                        ),
                        self.config.asr_model,
                    ),
                    action_id="settings.subtitles",
                    action_text=self.tr("Open Transcription Settings"),
                ),
                action=lambda: reveal_settings(self, "subtitles"),
            )
            return

        # Build and start worker
        self._begin_tool_run(len(video_files))
        self._total_files = len(video_files)
        self.log_widget.clear_log()
        self.progress_widget.reset()

        worker = SubtitleGenWorker(
            self.config,
            video_files,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
        )
        self.worker_thread = worker

        # Wire signals
        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        worker.error.connect(self._on_run_error)
        # Lifecycle: free the QThread on real thread exit (not on queue_finished,
        # which fires just before the thread ends). Clears the handle so the
        # reentrancy guard and iter_close_workers see no stale worker.
        worker.finished.connect(self._on_worker_finished)

        self.generate_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    def _collect_video_files(self) -> list[Path]:
        """Return supported media files to process, or [] on validation failure."""
        if not self.file_selector.isHidden():
            path_str = self.file_selector.path_or_none()
            if path_str is None:
                self.show_screen_issue(
                    ScreenIssue(summary=self.tr("Choose a video or audio file before generating subtitles."))
                )
                return []
            p = Path(path_str)
            if not p.is_file():
                self.show_screen_issue(
                    ScreenIssue(summary=self.tr("That media file no longer exists."), details=path_str)
                )
                return []
            return [p]
        else:
            path_str = self.folder_selector.path_or_none()
            if path_str is None:
                self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a folder before generating subtitles.")))
                return []
            folder = Path(path_str)
            if not folder.is_dir():
                self.show_screen_issue(ScreenIssue(summary=self.tr("That folder no longer exists."), details=path_str))
                return []
            try:
                files = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in _MEDIA_EXTENSIONS)
            except OSError:
                self.show_screen_issue(ScreenIssue(summary=self.tr("That folder could not be read."), details=path_str))
                return []
            if not files:
                self.show_screen_issue(
                    ScreenIssue(summary=self.tr("No video or audio files were found in that folder."))
                )
                return []
            return files

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Transcribing file %1 of %2"), str(idx + 1), str(self._total_files))
        )

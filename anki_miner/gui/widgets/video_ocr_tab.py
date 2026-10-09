"""Utilities → Video OCR: read burned-in dialogue from a video region into a timed .srt.

Guard contract:
- Engine probe fails → Read Subtitles disabled, notice visible; the setup card offers the download.
- No region yet → Read Subtitles raises a screen issue whose action opens the region dialog.
- Output directory not writable → Read Subtitles aborts with a screen issue.

numpy-free at import: the region dialog and the scan stack load on first use
(``_open_region_dialog``, ``VideoOcrWorker._process_item``), never at app startup.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.utils.run_options import RunOptionsMixin
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import FormPanel, PageWidth, ScreenIssue, configure_card_layout
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.workers.video_ocr_worker import VideoOcrWorker
from anki_miner.services.asr import onnx_pack_installer
from anki_miner.services.video_ocr import model_installer, runtime
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.region import Region
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.widgets.dialogs.ocr_region_dialog import OcrRegionDialog

_VIDEO_EXTENSIONS = FilePairMatcher.VIDEO_EXTENSIONS
_VIDEO_FILTER = "Video Files (" + " ".join(f"*{e}" for e in sorted(_VIDEO_EXTENSIONS)) + ");;All Files (*)"


@dataclass(frozen=True)
class _EngineState:
    """Probe verdict: truthy when the tool can run; ``runtime_ready`` picks the setup card's offer."""

    available: bool
    runtime_ready: bool

    def __bool__(self) -> bool:
        return self.available


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_EXTENSIONS


def _pct(value: float) -> str:
    return str(round(value * 100))


class VideoOcrTab(RunOptionsMixin, _ToolTabBase):
    """Draw the subtitle region once, then read it from one video or a folder of parts.

    Signals:
        run_options_changed: Emitted with a new ``AnkiMinerConfig`` when the
            region changes (so ``video_ocr_region`` persists). Overwrite is
            transient on purpose.
        video_ocr_install_requested: Emitted when the user clicks the setup
            card's download button. The install targets are resolved by the
            wiring, not this tab.
    """

    PAGE_WIDTH = PageWidth.PAGE
    TASK_ID = "tools.videoocr"
    TASK_OWNER = CapabilityTarget("subtitles", "videoocr")
    OUTPUT_HISTORY_KEY = "tools.videoocr.output"
    _PROBE_NAME = "Video OCR"

    run_options_changed = pyqtSignal(object)  # Emits AnkiMinerConfig
    video_ocr_install_requested = pyqtSignal()

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
        self._total_files = 0
        self._cancelled = False
        self._engine_is_available = False
        self._runtime_ready = False
        #: True between a download click and its landing, so a probe landing
        #: meanwhile cannot re-enable the button or clobber "Installing…".
        self._install_active = False
        self._region: Region | None = Region.from_config(config.video_ocr_region)
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
            partial=self.tr("Finished with errors — see log"),
            run_problem=self.tr("Some videos could not be read."),
            run_problem_single=self.tr("This video could not be read."),
            complete_template=self.tr("Complete — %1 files processed"),
            complete_skipped_template=self.tr("Complete — %1 processed, %2 skipped"),
            all_skipped_template=self.tr("No subtitles saved — all %1 skipped; see log."),
            select_output_folder=self.tr("Select Output Folder"),
            output_default=self.tr("Next to each video"),
            task_title=self.tr("Video OCR"),
        )
        self._setup_ui()
        self._show_region(None)
        self._refresh_engine_state()

    # ------------------------------------------------------------------
    # Base hooks
    # ------------------------------------------------------------------

    def _item_total(self) -> int:
        return self._total_files

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Reading subtitles in file %1 of %2"), str(idx + 1), str(self._total_files))
        )

    def _apply_mode(self, single: bool) -> None:
        self.file_selector.setVisible(single)
        self.folder_selector.setVisible(not single)

    def _probe_engine(self) -> Callable[[], object]:
        config = self.config
        return lambda: self._compute_engine_available(config)

    @staticmethod
    def _compute_engine_available(config: AnkiMinerConfig) -> _EngineState:
        """Cheap probe: onnxruntime importable + both models on disk. Never loads a model."""
        ready = runtime.runtime_ready(config.onnx_pack_root)
        return _EngineState(ready and model_installer.is_installed(config.video_ocr_models_root), ready)

    def _apply_probe_result(self, result: object) -> None:
        super()._apply_probe_result(result)
        self._runtime_ready = bool(getattr(result, "runtime_ready", False))
        self._apply_setup_state()

    def _typed_problem_summary(self, exc: BaseException) -> str | None:
        """The fatal queue error (E13): the files are present but the engine will not start, so no repair."""
        if isinstance(exc, EngineLoadError):
            return self.tr("The OCR engine could not start on this computer. The details say why.")
        return None

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new config; re-probe only when something other than the region changed."""
        old, self.config = self.config, config
        if not still_running(self.worker_thread):
            region = Region.from_config(config.video_ocr_region)
            if region != self._region:
                self._region = region
                self._show_region(None)
        if dataclasses.replace(old, video_ocr_region=config.video_ocr_region) != config:
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
        layout.addWidget(self._create_region_section())
        layout.addWidget(self._create_setup_section())
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

        self.engine_notice_label = QLabel(
            self.tr("The OCR engine is not installed yet. Download it from the setup card below.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        self._build_mode_row(
            layout,
            mode_label=self.tr("Mode:"),
            single_label=self.tr("Single File"),
            folder_label=self.tr("Folder"),
            single_tip=self.tr("Read one video."),
            folder_tip=self.tr("Read every video in a folder with the same region."),
        )

        self.file_selector = FileSelector(
            label=self.tr("Video File:"),
            file_mode=True,
            file_filter=_VIDEO_FILTER,
            history_key="tools.videoocr.inputs",
            drop_validator=accepts_suffixes(_VIDEO_EXTENSIONS, self.tr("This field takes a video file.")),
        )
        layout.addWidget(self.file_selector)

        self.folder_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
            history_key="tools.videoocr.inputs",
        )
        self.folder_selector.hide()
        layout.addWidget(self.folder_selector)

        group.setLayout(layout)
        return group

    def _create_region_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Subtitle region")))

        row = QHBoxLayout()
        row.setSpacing(SPACING.xs)
        self.region_label = QLabel("")
        self.region_label.setWordWrap(True)
        row.addWidget(self.region_label, 1)
        self.region_thumbnail = QLabel()
        self.region_thumbnail.hide()
        row.addWidget(self.region_thumbnail)
        self.set_region_button = ModernButton(self.tr("Set region…"), variant="secondary")
        self.set_region_button.clicked.connect(self._on_set_region)
        row.addWidget(self.set_region_button)
        layout.addLayout(row)

        helper = QLabel(
            self.tr("Draw a box around where the dialogue appears. One region is used for every video in a folder.")
        )
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        layout.addWidget(helper)

        group.setLayout(layout)
        return group

    def _create_setup_section(self) -> QFrame:
        """The in-app engine download; hidden once the probe finds the runtime and both models."""
        self.setup_card = QFrame()
        self.setup_card.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)
        layout.addWidget(SectionHeader(self.tr("OCR engine")))

        self.install_button = ModernButton(self.tr("Download OCR engine"), variant="secondary")
        self.install_button.clicked.connect(self._on_install_clicked)

        # A plain fact, not a success: the neutral grey Manga OCR's setup card uses (E08).
        self.install_status_label = QLabel("")
        self.install_status_label.setObjectName("validation-status")
        self.install_status_label.setProperty("status", "info")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.install_button)
        row.addWidget(self.install_status_label)
        row.addStretch()
        layout.addLayout(row)

        self.setup_card.setLayout(layout)
        return self.setup_card

    def _create_output_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Output")))

        self._build_output_row(
            layout,
            output_label=self.tr("Output:"),
            choose_label=self.tr("Choose Folder…"),
            reset_label=self.tr("Reset"),
        )
        self.choose_output_button.setToolTip(
            self.tr("Each .srt is saved next to its video unless you choose a folder.")
        )

        # Deliberately NOT persisted. Off-by-default each launch is the safety
        # property: a remembered destructive default carries no reminder. Pinned
        # by tests/unit/test_run_option_persistence.py::test_overwrite_is_never_persisted.
        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing SRT files"))
        self.overwrite_checkbox.setToolTip(
            self.tr("When unchecked, videos that already have an .srt file are skipped, not overwritten.")
        )
        layout.addWidget(self.overwrite_checkbox)

        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6)."""
        self.ocr_button = ModernButton(self.tr("Read Subtitles"), variant="primary")
        self.ocr_button.clicked.connect(self._on_ocr)
        # Base slots (queue-finished re-enable) act on the tool's primary button.
        self._primary_button = self.ocr_button

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Region
    # ------------------------------------------------------------------

    def _show_region(self, thumbnail: QPixmap | None) -> None:
        if self._region is None:
            self.region_label.setText(self.tr("No region set yet."))
            self.region_thumbnail.hide()
            return
        r = self._region
        self.region_label.setText(
            tr_format(
                self.tr("%1% from the left, %2% from the top, %3% wide, %4% tall"),
                _pct(r.x),
                _pct(r.y),
                _pct(r.w),
                _pct(r.h),
            )
        )
        if thumbnail is None:
            self.region_thumbnail.hide()
        else:
            self.region_thumbnail.setPixmap(thumbnail)
            self.region_thumbnail.show()

    def _set_region(self, region: Region, thumbnail: QPixmap | None = None) -> None:
        # Normalised to the persisted precision: the config refresh that persist_run_options
        # triggers must compare equal, or update_config would drop the thumbnail just shown.
        region = Region(*region.as_config())
        self._region = region
        self._show_region(thumbnail)
        self.persist_run_options(video_ocr_region=region.as_config())

    def _on_set_region(self) -> None:
        self.clear_screen_issue()
        if self.file_selector.isHidden():
            folder = self.folder_selector.path_or_none()
            if folder is None or not Path(folder).is_dir():
                self.show_screen_issue(
                    ScreenIssue(summary=self.tr("Choose a folder of videos first, then set the region on one of them."))
                )
                return

            # Held for the listing, so a slow share cannot stack two listings and two dialogs.
            self.set_region_button.setEnabled(False)

            def _on_files(files: list[Path]) -> None:
                # Called on every outcome: videos, an empty folder, a failed listing.
                self.set_region_button.setEnabled(True)
                if files:
                    self._open_region_dialog(files[0])

            # Not a run's listing: it must not hold or release Read Subtitles' scan guard.
            self._scan_folder_async(
                Path(folder),
                _is_video,
                _on_files,
                empty_summary=self.tr("No videos were found in that folder."),
                failed_summary=self.tr("That folder could not be scanned."),
                guards_primary=False,
            )
            return
        path = self.file_selector.path_or_none()
        if path is None or not Path(path).is_file():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Choose a video first, then set the region on one of its frames."))
            )
            return
        self._open_region_dialog(Path(path))

    def _open_region_dialog(self, video: Path) -> None:
        # The dialog brings numpy and the scan stack; load it on first use, not at app startup.
        from anki_miner.gui.widgets.dialogs.ocr_region_dialog import OcrRegionDialog

        # A fresh dialog per open: done() latches its _closing guard for good.
        dialog = OcrRegionDialog(self.config, video, self._region, worker_parent=self, parent=self)
        dialog.accepted.connect(lambda d=dialog: self._on_region_accepted(d))
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()

    def _on_region_accepted(self, dialog: OcrRegionDialog) -> None:
        region = dialog.region()
        if region is not None:
            self._set_region(region, dialog.region_thumbnail())

    # ------------------------------------------------------------------
    # Setup card
    # ------------------------------------------------------------------

    def _apply_setup_state(self) -> None:
        self.setup_card.setVisible(not self._engine_is_available)
        if self._engine_is_available or self._install_active:
            return
        offerable = self._runtime_ready or onnx_pack_installer.onnx_pack_supported()
        self.install_button.setVisible(offerable)
        self.install_button.setEnabled(offerable and not still_running(self.worker_thread))
        if self._runtime_ready:
            self.install_button.setText(self.tr("Download OCR models"))
            self.install_button.setToolTip(
                self.tr("Downloads the two OCR models (about 33 MB) into Anki Miner's folder.")
            )
        else:
            self.install_button.setText(self.tr("Download OCR engine"))
            self.install_button.setToolTip(
                self.tr("Downloads the OCR runtime and its two models (about 50 MB) into Anki Miner's folder.")
            )
        self.set_install_status(
            self.tr("Not installed")
            if offerable
            else self.tr(
                'Not available on this platform. With a pip install of Anki Miner, run: pip install "anki-miner[ocr]"'
            )
        )

    def _on_install_clicked(self) -> None:
        # Guard, button and status before the emit: the controller may refuse
        # synchronously and call notify_install_finished(False) inside it.
        self._install_active = True
        self.install_button.setEnabled(False)
        self.set_install_status(self.tr("Installing…"))
        self.video_ocr_install_requested.emit()

    def set_install_status(self, text: str) -> None:
        """Set the status line beside the download button."""
        FormPanel.set_status_text(self.install_status_label, text)

    def notify_install_finished(self, ok: bool) -> None:
        """Clear the in-flight guard; re-probe only on success (a failure keeps its message)."""
        self._install_active = False
        self.install_button.setEnabled(not still_running(self.worker_thread))
        if ok:
            self._refresh_engine_state()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_ocr(self) -> None:
        if not self._engine_is_available or still_running(self.worker_thread):
            return
        self.clear_screen_issue()
        if self._region is None:
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Set the subtitle region before reading subtitles."),
                    action_id="tools.videoocr.region",
                    action_text=self.tr("Set region…"),
                ),
                action=self._on_set_region,
            )
            return
        if not self.file_selector.isHidden():
            files = self._collect_single_video_file()
            if files:
                self._continue(files)
            return
        # Disabled for the folder scan, so a second click cannot start a second scan.
        self.ocr_button.setEnabled(False)

        def _on_files(files: list[Path]) -> None:
            if not files:
                self.ocr_button.setEnabled(True)
                return
            self._continue(files)

        self._collect_folder_video_files_async(_on_files)

    def _continue(self, files: list[Path]) -> None:
        out_dir = self._custom_output_dir
        # None writes each .srt next to its video, so check the first video's folder.
        check_dir = out_dir if out_dir is not None else files[0].parent
        if not self._output_dir_writable(check_dir, self.tr("Output folder is not writable.")):
            self.ocr_button.setEnabled(True)
            return
        assert self._region is not None
        self._begin_tool_run(len(files))
        self._total_files = len(files)
        self.log_widget.clear_log()
        self.progress_widget.reset()
        worker = VideoOcrWorker(
            self.config, files, self._region, output_dir=out_dir, overwrite=self.overwrite_checkbox.isChecked()
        )
        self._start_queue_worker(worker)

    def _collect_single_video_file(self) -> list[Path]:
        """Single-file mode: return [video], or [] on validation failure."""
        path_str = self.file_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a video before reading subtitles.")))
            return []
        p = Path(path_str)
        if not p.is_file():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That video file no longer exists."), details=path_str))
            return []
        return [p]

    def _collect_folder_video_files_async(self, on_files: Callable[[list[Path]], None]) -> None:
        """Folder mode: list the folder's videos off the GUI thread, then call ``on_files`` ([] on refusal)."""
        path_str = self.folder_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a folder before reading subtitles.")))
            on_files([])
            return
        folder = Path(path_str)
        if not folder.is_dir():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder no longer exists."), details=path_str))
            on_files([])
            return

        self._scan_folder_async(
            folder,
            _is_video,
            on_files,
            empty_summary=self.tr("No videos were found in that folder."),
            failed_summary=self.tr("That folder could not be scanned."),
        )

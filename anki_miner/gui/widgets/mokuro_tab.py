"""Manga OCR tab — a GUI over mokuro (Utilities → Manga OCR).

Pick a folder — one volume of page images, or a series folder of volumes —
and mokuro writes ``<Volume>.mokuro`` beside each one, which Reading → Manga
then mines. No output folder: mokuro's own sibling rule is the consumer's
contract (``services/reading/detector.py``).

Structure and idioms are cloned from
:mod:`anki_miner.gui.widgets.download_tab` (options persistence via
``config_changed``, off-thread availability probe, worker lifecycle); the
folder scan runs off-thread like Condense's folder mode.

Guard contract:
- mokuro not found → Run OCR disabled, notice visible (Settings has the installer).
- Folder missing / empty / not writable → Run aborts with a screen issue.

Worker contract:
- Worker stored on ``self.worker_thread``; the scan's ``run_off_thread`` handle
  on ``self._scan_worker``; both yielded by ``iter_close_workers()``.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout, field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.mokuro_worker import MokuroWorker
from anki_miner.services.mokuro_runner import MokuroOptions
from anki_miner.services.mokuro_volumes import MokuroVolume, scan_volumes
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.mokuro_resolver import mokuro_available

if TYPE_CHECKING:
    from anki_miner.gui.workers.base_worker import CancellableWorker

logger = logging.getLogger(__name__)


class MokuroTab(_ToolTabBase):
    """Tab that runs mokuro over a volume or series folder.

    Deliberately has NO output section: mokuro writes each ``.mokuro`` beside
    its input, so ``OUTPUT_HISTORY_KEY`` is empty, ``_custom_output_dir`` stays
    ``None``, and the base's ``output_location_label`` / ``clear_output_button``
    are never created — only the never-connected ``_on_choose_output`` /
    ``_on_clear_output`` touch them.

    Signals:
        config_changed: Emitted with a new ``AnkiMinerConfig`` when the user
            toggles "Use GPU when available" so ``mokuro_use_gpu`` persists.
            The Redo box is transient on purpose (an overwrite-class option).
    """

    PAGE_WIDTH = PageWidth.PAGE
    TASK_ID = "tools.mokuro"
    TASK_OWNER = CapabilityTarget("subtitles", "mokuro")
    #: mokuro writes beside its input; there is no output folder to remember.
    OUTPUT_HISTORY_KEY = ""

    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig

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
        self._seeding = False
        self.worker_thread = None
        self._custom_output_dir = None
        self._cancelled = False
        self._total_volumes = 0
        self._run_volumes: list[MokuroVolume] = []
        self._mokuro_is_available = False
        self._scan_worker: SingleCallWorker | None = None
        #: True between a Run's folder scan dispatch and its landing, so a
        #: second click cannot queue a second scan.
        self._scan_pending_run = False
        self._preview_generation = 0
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
            run_problem=self.tr("Some volumes could not be processed."),
            complete_template=self.tr("Complete — %1 volume(s) processed"),
            complete_skipped_template=self.tr("Complete — %1 processed, %2 already had a .mokuro file"),
            all_skipped_template=self.tr(
                "Nothing processed — all %1 already have a .mokuro file. Tick Redo to run OCR again."
            ),
            select_output_folder="",
            output_default="",
            task_title=self.tr("Manga OCR"),
        )
        self._setup_ui()
        self._apply_config_defaults()
        self._refresh_engine_state()

    def _item_total(self) -> int:
        return self._total_volumes

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new config; re-probe only when something other than the run option changed."""
        old_config, self.config = self.config, config
        idle = self.worker_thread is None or not self.worker_thread.isRunning()
        if idle and self.config.mokuro_use_gpu != self.gpu_checkbox.isChecked():
            self._apply_config_defaults()
        masked = dataclasses.replace(old_config, mokuro_use_gpu=config.mokuro_use_gpu)
        if masked != config:
            self._refresh_engine_state()

    def notify_install_finished(self) -> None:
        """Re-run the availability guard after the in-app installer succeeded.

        Separate from :meth:`update_config` on purpose: an install changes no
        config value, so the mask there (a GPU-only change must not re-probe)
        would swallow a ``config_refreshed`` carrying the same config.
        """
        self._refresh_engine_state()

    def _apply_config_defaults(self) -> None:
        self._seeding = True
        try:
            self.gpu_checkbox.setChecked(self.config.mokuro_use_gpu)
        finally:
            self._seeding = False

    def _on_gpu_changed(self, checked: bool) -> None:
        if self._seeding:
            return
        new_config = replace(self.config, mokuro_use_gpu=checked)
        if new_config == self.config:
            return
        self.config = new_config
        self.config_changed.emit(new_config)

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
        layout.addWidget(self._create_options_section())
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
        layout.addWidget(SectionHeader(self.tr("Manga")))

        self.engine_notice_label = QLabel(
            self.tr(
                "mokuro not found. Install it in Settings → Transcription & Alignment → Manga OCR, "
                "or set its path there."
            )
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        desc = QLabel(
            self.tr(
                "Run mokuro's OCR on a folder of page images, or a series folder of volumes. "
                "Each volume gets a .mokuro file beside it for Reading → Manga."
            )
        )
        desc.setObjectName("helper-text")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.folder_selector = FileSelector(
            label=self.tr("Folder:"),
            file_mode=False,
            file_filter="",
            label_width=field_label_width(self.tr("Folder:")),
            history_key="tools.mokuro.inputs",
        )
        self.folder_selector.setToolTip(
            self.tr(
                "A folder of page images (one volume), or a folder whose subfolders and .cbz/.zip files are volumes."
            )
        )
        self.folder_selector.path_changed.connect(self._on_folder_changed)
        layout.addWidget(self.folder_selector)

        self.volumes_label = QLabel("")
        self.volumes_label.setObjectName("helper-text")
        self.volumes_label.setWordWrap(True)
        layout.addWidget(self.volumes_label)

        group.setLayout(layout)
        return group

    def _create_options_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)
        layout.addWidget(SectionHeader(self.tr("Options")))

        self.gpu_checkbox = QCheckBox(self.tr("Use GPU when available"))
        self.gpu_checkbox.setToolTip(self.tr("Untick to force CPU even when mokuro's torch build can use a GPU."))
        self.gpu_checkbox.toggled.connect(self._on_gpu_changed)
        layout.addWidget(self.gpu_checkbox)

        self.redo_checkbox = QCheckBox(self.tr("Redo volumes that already have a .mokuro file"))
        self.redo_checkbox.setToolTip(
            self.tr("Runs OCR again from scratch, ignoring mokuro's cached page results. Off each launch.")
        )
        layout.addWidget(self.redo_checkbox)

        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        self.run_button = ModernButton(self.tr("Run OCR"), variant="primary")
        self.run_button.clicked.connect(self._on_run)
        self._primary_button = self.run_button
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine / availability state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        config = self.config
        self.run_button.setEnabled(False)
        if self._suppress_optional_startup:
            return

        def _on_error(message: str) -> None:
            logger.warning("mokuro availability probe failed: %s", message)
            self._apply_probe_result(False)

        self._run_availability_scan(lambda: self._compute_mokuro_available(config), self._apply_probe_result, _on_error)

    def _apply_probe_result(self, result: object) -> None:
        # A probe scheduled before a run started can land after it did. The run
        # owns the button for its whole duration — including the folder scan
        # that precedes the worker, when worker_thread is still None.
        self._mokuro_is_available = bool(result)
        self.engine_notice_label.setVisible(not self._mokuro_is_available)
        self.run_button.setEnabled(
            self._mokuro_is_available and not still_running(self.worker_thread) and not self._scan_pending_run
        )

    def _mokuro_ready(self) -> bool:
        return self._mokuro_is_available

    @staticmethod
    def _compute_mokuro_available(config: AnkiMinerConfig) -> bool:
        """Resolver-only probe (never spawns mokuro: its import costs seconds)."""
        return mokuro_available(config.mokuro_location, config.uv_root)

    # ------------------------------------------------------------------
    # Folder preview
    # ------------------------------------------------------------------

    def _on_folder_changed(self, path: str) -> None:
        # Bumped before the early return too, so a scan already in flight for a
        # previous path is dropped when the user clears the field.
        self._preview_generation += 1
        generation = self._preview_generation
        folder = Path(path) if path else None
        if folder is None or not folder.is_dir():
            self.volumes_label.setText("")
            return

        def _apply(result: object) -> None:
            if generation != self._preview_generation:
                return
            volumes = cast("list[MokuroVolume]", result)
            done = sum(1 for v in volumes if v.already_processed)
            if not volumes:
                self.volumes_label.setText(self.tr("No manga volumes found in this folder."))
            elif done:
                self.volumes_label.setText(
                    tr_format(self.tr("%1 volume(s) found, %2 already processed."), len(volumes), done)
                )
            else:
                self.volumes_label.setText(tr_format(self.tr("%1 volume(s) found."), len(volumes)))

        def _on_error(_msg: str) -> None:
            if generation == self._preview_generation:
                self.volumes_label.setText(self.tr("This folder could not be read."))

        self._scan_worker = run_off_thread(self, lambda: scan_volumes(folder), _apply, _on_error)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        if not self._mokuro_ready():
            return
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return
        # Not conjoined with still_running(self._scan_worker): a click delivered
        # after the scan thread stopped but before its queued result_ready ran
        # would pass both and start a second scan — two workers over the same
        # volumes, the first orphaned. The flag alone spans that window; it is
        # cleared by both scan continuations.
        if self._scan_pending_run:
            return
        self.clear_screen_issue()
        self.log_widget.clear_log()
        self.progress_widget.reset()

        folder_str = self.folder_selector.path_or_none()
        if folder_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a manga folder before running OCR.")))
            return
        folder = Path(folder_str)
        if not folder.is_dir():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder no longer exists."), details=folder_str))
            return
        # mokuro writes the .mokuro sidecars beside the input.
        if not os.access(folder, os.W_OK):
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Manga folder is not writable."),
                    details=tr_format(
                        self.tr("mokuro writes its .mokuro files beside the volumes; check permissions for %1."),
                        folder_str,
                    ),
                )
            )
            return

        self._scan_pending_run = True
        self.run_button.setEnabled(False)

        def _on_scanned(result: object) -> None:
            self._scan_pending_run = False
            volumes = cast("list[MokuroVolume]", result)
            if not volumes:
                self.run_button.setEnabled(True)
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr("No manga volumes found in that folder."),
                        details=self.tr(
                            "A volume is a folder of page images (.jpg, .png, .webp, .avif) or a .cbz/.zip "
                            "archive. A series folder holds one of those per volume."
                        ),
                    )
                )
                return
            self._start_worker(volumes)

        def _on_error(msg: str) -> None:
            self._scan_pending_run = False
            self.run_button.setEnabled(True)
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder could not be read."), details=msg))

        self._scan_worker = run_off_thread(self, lambda: scan_volumes(folder), _on_scanned, _on_error)

    def _start_worker(self, volumes: list[MokuroVolume]) -> None:
        # Totals first: _begin_tool_run publishes the run, and the registry's
        # first read of _item_total() must already be this run's count.
        self._total_volumes = len(volumes)
        self._run_volumes = volumes
        self._begin_tool_run(len(volumes))
        for volume in volumes:
            note = self.tr(" (already processed)") if volume.already_processed else ""
            self.log_widget.append_info(f"{volume.source.name}{note}")

        worker = MokuroWorker(
            self.config,
            volumes,
            options=MokuroOptions(force_cpu=not self.gpu_checkbox.isChecked(), no_cache=self.redo_checkbox.isChecked()),
            skip_processed=not self.redo_checkbox.isChecked(),
        )
        self.worker_thread = worker
        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        worker.error.connect(self._on_run_error)
        worker.finished.connect(self._on_worker_finished)
        self.run_button.setEnabled(False)
        self.cancel_button.show()
        worker.start()

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(tr_format(self.tr("Volume %1 of %2"), str(idx + 1), str(self._total_volumes)))
        if 0 <= idx < len(self._run_volumes):
            self.log_widget.append_info(str(self._run_volumes[idx].source))

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        yield from super().iter_close_workers()
        if still_running(self._scan_worker):
            assert self._scan_worker is not None
            yield self._scan_worker

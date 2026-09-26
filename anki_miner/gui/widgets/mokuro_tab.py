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
- mokuro not found → Run OCR disabled, notice visible (the setup card below installs it).
- Folder missing / empty / not writable → Run aborts with a screen issue.

Worker contract:
- Worker stored on ``self.worker_thread``; the scan's ``run_off_thread`` handle
  on ``self._scan_worker``; both yielded by ``iter_close_workers()``.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, cast

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import FormPanel, PageWidth, ScreenIssue, configure_card_layout, field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.mokuro_worker import MokuroWorker
from anki_miner.services import mokuro_installer
from anki_miner.services.mokuro_runner import MokuroOptions
from anki_miner.services.mokuro_volumes import MokuroVolume, scan_volumes
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.mokuro_resolver import mokuro_available

if TYPE_CHECKING:
    from anki_miner.gui.workers.base_worker import CancellableWorker


#: Debounce for persisting an edited mokuro-executable path: FileSelector's
#: ``path_changed`` fires on every keystroke, and ``update_config`` re-probes
#: on any location change, so writing on every keystroke would re-run the
#: availability probe per character. Same interval as the Settings auto-save
#: debounce (``settings_tab.py``'s ``_AUTOSAVE_DEBOUNCE_MS``).
_MOKURO_LOCATION_DEBOUNCE_MS = 1000


class MokuroTab(_ToolTabBase):
    """Tab that runs mokuro over a volume or series folder.

    Deliberately has NO output section: mokuro writes each ``.mokuro`` beside
    its input, so ``OUTPUT_HISTORY_KEY`` is empty, ``_custom_output_dir`` stays
    ``None``, and the base's ``output_location_label`` / ``clear_output_button``
    are never created — only the never-connected ``_on_choose_output`` /
    ``_on_clear_output`` touch them.

    Signals:
        config_changed: Emitted with a new ``AnkiMinerConfig`` when the user
            toggles "Use GPU when available" (so ``mokuro_use_gpu`` persists)
            or edits the mokuro executable path (debounced; so
            ``mokuro_location`` persists). The Redo box is transient on
            purpose (an overwrite-class option).
        mokuro_install_requested: Emitted when the user clicks "Install
            mokuro" / "Reinstall mokuro" on the setup card. The managed
            install targets (``config.bin_root`` for uv, ``config.uv_root``
            for the environment) are resolved by the wiring, not this tab.
    """

    PAGE_WIDTH = PageWidth.PAGE
    TASK_ID = "tools.mokuro"
    TASK_OWNER = CapabilityTarget("subtitles", "mokuro")
    #: mokuro writes beside its input; there is no output folder to remember.
    OUTPUT_HISTORY_KEY = ""

    _PROBE_NAME = "mokuro"

    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig
    mokuro_install_requested = pyqtSignal()

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
        self._mokuro_supported = False if suppress_optional_startup else mokuro_installer.mokuro_install_supported()
        #: True between an Install click and its landing, so a probe running
        #: concurrently (an unrelated config change) cannot re-enable the
        #: button or clobber the "Installing…" status.
        self._mokuro_install_active = False
        self._mokuro_location_debounce_ms = _MOKURO_LOCATION_DEBOUNCE_MS
        self._mokuro_location_timer = QTimer(self)
        self._mokuro_location_timer.setSingleShot(True)
        self._mokuro_location_timer.timeout.connect(self._commit_mokuro_location)
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
            partial=self.tr("Finished with errors — see log"),
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
        """Adopt a new config; re-probe only when something other than the run option changed.

        Reseeding the location field is skipped while an edit is still
        debouncing (``_mokuro_location_timer.isActive()``): a same-tab
        edit-then-toggle (e.g. type a path, tick GPU within the debounce
        window) round-trips through ``config_changed`` -> ``window.update_config``
        -> ``config_refreshed`` and lands back here carrying the GPU change
        but the OLD location (the debounce hadn't committed it yet). Reseeding
        on that echo would overwrite the selector with the stale location and
        the pending edit would be lost when the timer then found nothing
        changed. The selector stays the source of truth until the debounce
        commits, and :meth:`_commit_mokuro_location` folds onto ``self.config``
        as it stands then — which by that point already carries this echo's
        GPU change.
        """
        old_config, self.config = self.config, config
        idle = self.worker_thread is None or not self.worker_thread.isRunning()
        if idle and self.config.mokuro_use_gpu != self.gpu_checkbox.isChecked():
            self._apply_gpu_default()
        if (
            idle
            and not self._mokuro_location_timer.isActive()
            and self.config.mokuro_location != self._current_mokuro_location()
        ):
            self._apply_mokuro_location_default()
        masked = dataclasses.replace(old_config, mokuro_use_gpu=config.mokuro_use_gpu)
        if masked != config:
            self._refresh_engine_state()

    def flush_pending_edits(self) -> None:
        """Stop the debounce and commit a pending mokuro-path edit immediately.

        Called from the app's close path (alongside the Settings auto-save
        flush) so a path typed just before quitting is not silently dropped —
        without this, closing before the 1000 ms debounce lands would tear
        down the tab with the edit never persisted.
        """
        if self._mokuro_location_timer.isActive():
            self._mokuro_location_timer.stop()
            self._commit_mokuro_location()

    def notify_install_finished(self, ok: bool) -> None:
        """Clear the in-flight guard and re-enable Install; re-probe only on success.

        Called on BOTH outcomes, unlike the pre-move ``notify_install_finished()``:
        this now also does the job the retired Settings panel's
        ``notify_mokuro_install_finished`` did for a FAILED install, since the
        setup card lives here now. On failure the worker's message is already
        on ``mokuro_status_label`` (the caller's ``set_status`` writes it before
        this runs); a re-probe would overwrite it with "Not installed" within
        milliseconds, so only a successful install re-runs the guard.
        """
        self._mokuro_install_active = False
        self.install_mokuro_button.setEnabled(
            self._mokuro_supported and not still_running(self.worker_thread) and not self._scan_pending_run
        )
        if ok:
            self._refresh_engine_state()

    def _current_mokuro_location(self) -> Path | None:
        """The mokuro-executable path currently in the setup card's selector."""
        path = self.mokuro_selector.path_or_none()
        return Path(path) if path is not None else None

    def _apply_config_defaults(self) -> None:
        """Seed both fields from ``self.config``. Construction-time only:
        :meth:`update_config` seeds each independently so a pending location
        edit can be left alone while the GPU checkbox still updates."""
        self._apply_gpu_default()
        self._apply_mokuro_location_default()

    def _apply_gpu_default(self) -> None:
        self._seeding = True
        try:
            self.gpu_checkbox.setChecked(self.config.mokuro_use_gpu)
        finally:
            self._seeding = False

    def _apply_mokuro_location_default(self) -> None:
        self._seeding = True
        try:
            self.mokuro_selector.set_path(str(self.config.mokuro_location) if self.config.mokuro_location else "")
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

    def _on_mokuro_location_changed(self, _path: str) -> None:
        """Restart the debounce; :meth:`_commit_mokuro_location` reads the live path."""
        if self._seeding:
            return
        self._mokuro_location_timer.start(self._mokuro_location_debounce_ms)

    def _commit_mokuro_location(self) -> None:
        """Persist the setup card's path through ``config_changed``, if it changed."""
        new_location = self._current_mokuro_location()
        if new_location == self.config.mokuro_location:
            return
        new_config = replace(self.config, mokuro_location=new_location)
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
        layout.addWidget(self._create_setup_section())
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
            self.tr("mokuro not found. Install it in the Manga OCR setup section below, or set its path there.")
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

    def _create_setup_section(self) -> QFrame:
        """mokuro executable override + the in-app installer (uv-managed environment).

        Moved here from Settings → Transcription & Alignment: mokuro is not a
        subtitle tool, but this tab is the one screen that needs it, so its
        path override and Install button live beside the thing they enable
        instead of a page away from it.
        """
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)
        layout.addWidget(SectionHeader(self.tr("Manga OCR setup")))

        self.mokuro_selector = FileSelector(
            label=self.tr("mokuro executable:"),
            file_mode=True,
            file_filter="All Files (*)",
            label_width=field_label_width(self.tr("mokuro executable:")),
            placeholder=self.tr("Optional: path to the mokuro executable"),
        )
        mokuro_selector_helper = self.tr(
            "Optional: your own mokuro (pip/pipx). Leave blank to use the in-app "
            "install below or mokuro on your PATH."
        )
        self.mokuro_selector.setToolTip(mokuro_selector_helper)
        self.mokuro_selector.path_changed.connect(self._on_mokuro_location_changed)
        layout.addWidget(self.mokuro_selector)

        mokuro_selector_help_label = QLabel(mokuro_selector_helper)
        mokuro_selector_help_label.setObjectName("helper-text")
        mokuro_selector_help_label.setWordWrap(True)
        layout.addWidget(mokuro_selector_help_label)

        # Unlike alass, the button exists on every platform: an unsupported
        # platform disables it and says so, because the path override above is
        # still a usable route there.
        self.install_mokuro_button = ModernButton(self.tr("Install mokuro"), variant="secondary")
        self.install_mokuro_button.setToolTip(
            self.tr(
                "Downloads mokuro and its OCR engine into Anki Miner's folder "
                "— about 1 GB, up to 4 GB with NVIDIA GPU support."
            )
        )
        self.install_mokuro_button.setEnabled(self._mokuro_supported)
        self.install_mokuro_button.clicked.connect(self._on_mokuro_install_clicked)

        self.mokuro_status_label = QLabel("")
        self.mokuro_status_label.setObjectName("settings-save-status")

        button_row = QWidget()
        row = QHBoxLayout(button_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.install_mokuro_button)
        row.addWidget(self.mokuro_status_label)
        row.addStretch()
        layout.addWidget(button_row)

        if not self._mokuro_supported:
            self.set_mokuro_status(self.tr("Not available on this platform"))

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

    def _probe_engine(self) -> Callable[[], object]:
        """Probe mokuro availability (the Run OCR guard) for the current config."""
        config = self.config
        return lambda: self._compute_mokuro_available(config)

    def _apply_probe_result(self, result: object) -> None:
        # A probe scheduled before a run started can land after it did. The run
        # owns the button for its whole duration — including the folder scan
        # that precedes the worker, when worker_thread is still None.
        self._mokuro_is_available = bool(result)
        self.engine_notice_label.setVisible(not self._mokuro_is_available)
        self.run_button.setEnabled(
            self._mokuro_is_available and not still_running(self.worker_thread) and not self._scan_pending_run
        )
        self._apply_mokuro_setup_state(self._mokuro_is_available)

    def _mokuro_ready(self) -> bool:
        return self._mokuro_is_available

    @staticmethod
    def _compute_mokuro_available(config: AnkiMinerConfig) -> bool:
        """Resolver-only probe (never spawns mokuro: its import costs seconds)."""
        return mokuro_available(config.mokuro_location, config.uv_root)

    # ------------------------------------------------------------------
    # Setup card: mokuro path override + in-app install
    # ------------------------------------------------------------------

    def _apply_mokuro_setup_state(self, installed: bool) -> None:
        """Reflect whether mokuro is reachable on the setup card; re-enable Install.

        Ported 1:1 from the retired Settings-panel ``_apply_mokuro_state``:
        "installed" covers the managed uv environment, an explicit path
        override, and the user's own pip/pipx mokuro on PATH — the same chain
        :meth:`_compute_mokuro_available` resolves, so the two can never
        disagree. Install stays disabled while: an install is already in
        flight; the platform is unsupported (the construction-time "Not
        available on this platform" status is the only reason on screen for
        that then, and must not be overwritten with "Not installed"); or an
        OCR run/folder scan is in flight — reinstalling mid-run could pull the
        executable out from under the running worker.
        """
        if (
            self._mokuro_install_active
            or not self._mokuro_supported
            or still_running(self.worker_thread)
            or self._scan_pending_run
        ):
            self.install_mokuro_button.setEnabled(False)
            return
        self.install_mokuro_button.setEnabled(True)
        self.install_mokuro_button.setText(self.tr("Reinstall mokuro") if installed else self.tr("Install mokuro"))
        self.set_mokuro_status(self.tr("Installed") if installed else self.tr("Not installed"))

    def _refresh_mokuro_setup_state(self) -> None:
        """Re-apply the setup card's Install gating after a run/scan state change.

        Called at every point the run/scan-in-flight flags change without a
        fresh availability probe landing (run start, scan end, worker end), so
        Install disables for the run's duration and re-enables the moment it
        is over.
        """
        self._apply_mokuro_setup_state(self._mokuro_is_available)

    def _on_mokuro_install_clicked(self) -> None:
        """Disable Install in flight, show a pending status, and request the install."""
        self._mokuro_install_active = True
        self.install_mokuro_button.setEnabled(False)
        self.set_mokuro_status(self.tr("Installing…"))
        self.mokuro_install_requested.emit()

    def set_mokuro_status(self, text: str) -> None:
        """Set the mokuro status label text (shown beside the Install button)."""
        FormPanel.set_status_text(self.mokuro_status_label, text)

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
                    tr_format(self.tr("Volumes found: %1, already processed: %2."), len(volumes), done)
                )
            else:
                self.volumes_label.setText(tr_format(self.tr("Volumes found: %1."), len(volumes)))

        def _on_error(_msg: str) -> None:
            if generation == self._preview_generation:
                self.volumes_label.setText(self.tr("This folder could not be scanned."))

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
        self._refresh_mokuro_setup_state()

        def _on_scanned(result: object) -> None:
            self._scan_pending_run = False
            volumes = cast("list[MokuroVolume]", result)
            if not volumes:
                self.run_button.setEnabled(True)
                self._refresh_mokuro_setup_state()
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
            self._refresh_mokuro_setup_state()
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder could not be scanned."), details=msg))

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
        self._start_queue_worker(worker)
        # After start(), not before: still_running() reads worker.isRunning(),
        # which is False until the thread has actually started.
        self._refresh_mokuro_setup_state()

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(tr_format(self.tr("Volume %1 of %2"), str(idx + 1), str(self._total_volumes)))
        if 0 <= idx < len(self._run_volumes):
            self.log_widget.append_info(str(self._run_volumes[idx].source))

    def _on_worker_finished(self) -> None:
        """Release the QThread, then re-enable Install now the run is truly over.

        ``queue_finished`` can land while ``QThread.isRunning()`` still
        reports True (it fires from the worker thread just before the thread
        actually exits); ``finished`` is the base class's own hook for "the
        thread has actually exited", so it is the correct place to re-apply
        the setup card's run-in-flight gate.
        """
        super()._on_worker_finished()
        self._refresh_mokuro_setup_state()

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        yield from super().iter_close_workers()
        if still_running(self._scan_worker):
            assert self._scan_worker is not None
            yield self._scan_worker

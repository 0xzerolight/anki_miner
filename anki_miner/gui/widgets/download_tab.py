"""Download tab — standalone yt-dlp downloader (Utilities → Download).

Paste URLs (one per line, any yt-dlp-supported site), pick a format preset or
a raw yt-dlp format string, optionally grab subtitles and embed
thumbnail/metadata, and download into a folder of your choice. A plain
downloader tool: nothing here feeds the mining pipeline.

Structure and idioms are cloned from
:mod:`anki_miner.gui.widgets.condense_tab` (options persistence via
``config_changed``, off-thread availability probe, output-location row, worker
lifecycle) — this tab is a sibling.

Guard contract:
- yt-dlp not found → Download disabled, notice visible.
- Output directory not writable → Download aborts, error logged.

Worker contract:
- Worker stored on ``self.worker_thread``.
- ``iter_close_workers()`` yields the active worker for
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import QStandardPaths, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.workers.download_worker import DownloadWorker
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.services.media_downloader import FORMAT_PRESETS, DownloadOptions
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.ytdlp_resolver import ytdlp_available

logger = logging.getLogger(__name__)


class DownloadTab(_ToolTabBase):
    """Tab for downloading media from URLs via yt-dlp.

    Shared worker-signal slots, output-location slots, progress chrome, and the
    close contract live in :class:`~anki_miner.gui.widgets._tool_tab_base._ToolTabBase`.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.

    Signals:
        config_changed: Emitted with a new ``AnkiMinerConfig`` when the user
            edits a run option (preset / custom format / subs / embeds), so the
            host can persist ``downloader_*`` to ``gui_config.json`` and
            survive restart. Mirrors ``CondenseTab.config_changed``.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "tools.download"
    TASK_OWNER = CapabilityTarget("subtitles", "download")

    #: Where this tool last wrote — remembered separately from its inputs (D7).
    OUTPUT_HISTORY_KEY = "tools.download.output"

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
        # Suppresses the persist slot while _apply_config_defaults seeds the
        # option widgets (see CondenseTab for the rationale).
        self._seeding: bool = False
        self.worker_thread = None
        self._custom_output_dir: Path | None = None
        self._total_urls: int = 0
        self._run_urls: list[str] = []
        self._cancelled: bool = False
        # yt-dlp availability is cached per-config: resolving it re-hashes the
        # managed binary, so it must not run on every read. Recomputed only
        # here and in update_config().
        self._ytdlp_is_available: bool = False
        default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        self._default_download_dir = Path(default_dir) if default_dir else Path.home()
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
            run_problem=self.tr("Some URLs could not be downloaded."),
            complete_template=self.tr("Complete — %1 downloaded"),
            complete_skipped_template=self.tr("Complete — %1 downloaded, %2 already present"),
            all_skipped_template=self.tr("Nothing downloaded — all %1 already present in the download folder."),
            select_output_folder=self.tr("Select Download Folder"),
            output_default=str(self._default_download_dir),
            task_title=self.tr("Media download"),
        )

        self._setup_ui()
        self._apply_config_defaults()
        self._refresh_engine_state()

    def _item_total(self) -> int:
        return self._total_urls

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new application config (e.g. after the yt-dlp path changes).

        Only a non-``downloader_*`` field can make yt-dlp appear/disappear, so
        the availability probe (a managed-binary re-hash) is skipped when the
        incoming config differs solely in those fields. Option-widget defaults
        are re-seeded only when idle AND actually differing — a run in flight
        captured its own values, and a refresh must not stomp uncommitted edits.
        """
        old_config, self.config = self.config, config
        idle = self.worker_thread is None or not self.worker_thread.isRunning()
        if idle and self._options_differ_from_widgets():
            self._apply_config_defaults()
        downloader_fields = {f.name for f in dataclasses.fields(config) if f.name.startswith("downloader_")}
        masked = dataclasses.replace(old_config, **{name: getattr(config, name) for name in downloader_fields})
        if masked != config:
            self._refresh_engine_state()

    def _apply_config_defaults(self) -> None:
        """Seed the option widgets from the current config's persisted defaults."""
        self._seeding = True
        try:
            idx = self.preset_combo.findData(self.config.downloader_format_preset)
            self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.custom_format_edit.setText(self.config.downloader_custom_format)
            self.write_subs_checkbox.setChecked(self.config.downloader_write_subtitles)
            self.sub_langs_edit.setText(self.config.downloader_subtitle_langs)
            self.sub_langs_edit.setEnabled(self.config.downloader_write_subtitles)
            self.embed_thumbnail_checkbox.setChecked(self.config.downloader_embed_thumbnail)
            self.embed_metadata_checkbox.setChecked(self.config.downloader_embed_metadata)
        finally:
            self._seeding = False

    def _options_differ_from_widgets(self) -> bool:
        """Whether the config's downloader_* values differ from the live
        widgets, compared post-normalization (the form `_on_option_changed`
        writes), so uncommitted whitespace never counts as a difference."""
        return (
            self.config.downloader_format_preset != self.preset_combo.currentData()
            or self.config.downloader_custom_format != self.custom_format_edit.text().strip()
            or self.config.downloader_write_subtitles != self.write_subs_checkbox.isChecked()
            or self.config.downloader_subtitle_langs != (self.sub_langs_edit.text().strip() or "ja")
            or self.config.downloader_embed_thumbnail != self.embed_thumbnail_checkbox.isChecked()
            or self.config.downloader_embed_metadata != self.embed_metadata_checkbox.isChecked()
        )

    def _on_option_changed(self, *_: object) -> None:
        """Persist an edited run option to config so it survives restart."""
        if self._seeding:
            return
        new_config = replace(
            self.config,
            downloader_format_preset=str(self.preset_combo.currentData()),
            downloader_custom_format=self.custom_format_edit.text().strip(),
            downloader_write_subtitles=self.write_subs_checkbox.isChecked(),
            downloader_subtitle_langs=self.sub_langs_edit.text().strip() or "ja",
            downloader_embed_thumbnail=self.embed_thumbnail_checkbox.isChecked(),
            downloader_embed_metadata=self.embed_metadata_checkbox.isChecked(),
        )
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

        layout.addWidget(SectionHeader(self.tr("URLs")))

        # yt-dlp notice (shown when the executable is unavailable)
        self.engine_notice_label = QLabel(
            self.tr("yt-dlp not found. Install or update it in Settings → YouTube to enable downloads.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        input_desc = QLabel(self.tr("Download videos or audio from any site yt-dlp supports, without mining."))
        input_desc.setObjectName("helper-text")
        input_desc.setWordWrap(True)
        layout.addWidget(input_desc)

        self.url_input = QPlainTextEdit()
        self.url_input.setPlaceholderText(self.tr("One URL per line"))
        # Tab must move focus, not insert a literal tab (keyboard-only flow).
        self.url_input.setTabChangesFocus(True)
        self.url_input.setFixedHeight(self.url_input.fontMetrics().lineSpacing() * 6 + 16)
        layout.addWidget(self.url_input)

        group.setLayout(layout)
        return group

    def _create_options_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Options")))

        quality_row = QHBoxLayout()
        quality_row.setSpacing(SPACING.xs)
        quality_row.addWidget(QLabel(self.tr("Quality:")))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(self.tr("Best available"), "best")
        self.preset_combo.addItem(self.tr("Up to 1440p"), "1440p")
        self.preset_combo.addItem(self.tr("Up to 1080p"), "1080p")
        self.preset_combo.addItem(self.tr("Up to 720p"), "720p")
        self.preset_combo.addItem(self.tr("Audio only (MP3)"), "audio_mp3")
        self.preset_combo.addItem(self.tr("Audio only (M4A)"), "audio_m4a")
        self.preset_combo.currentIndexChanged.connect(self._on_option_changed)
        quality_row.addWidget(self.preset_combo)
        quality_row.addStretch()
        layout.addLayout(quality_row)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(SPACING.xs)
        custom_row.addWidget(QLabel(self.tr("Custom format:")))
        self.custom_format_edit = QLineEdit()
        self.custom_format_edit.setPlaceholderText(self.tr("Optional yt-dlp format string"))
        # editingFinished (not textChanged): persisting every keystroke would
        # write gui_config.json once per character.
        self.custom_format_edit.editingFinished.connect(self._on_option_changed)
        custom_row.addWidget(self.custom_format_edit, 1)
        layout.addLayout(custom_row)

        custom_hint = QLabel(self.tr("When set, the quality preset above is ignored."))
        custom_hint.setObjectName("helper-text")
        custom_hint.setWordWrap(True)
        layout.addWidget(custom_hint)

        subs_row = QHBoxLayout()
        subs_row.setSpacing(SPACING.xs)
        self.write_subs_checkbox = QCheckBox(self.tr("Download subtitles"))
        self.write_subs_checkbox.setToolTip(
            self.tr("Save subtitles next to the media file. Prefers manual subtitles, falls back to automatic.")
        )
        self.write_subs_checkbox.toggled.connect(self._on_write_subs_toggled)
        self.write_subs_checkbox.toggled.connect(self._on_option_changed)
        subs_row.addWidget(self.write_subs_checkbox)
        subs_row.addWidget(QLabel(self.tr("Languages:")))
        self.sub_langs_edit = QLineEdit()
        self.sub_langs_edit.setToolTip(self.tr("Comma-separated language codes, e.g. ja,en"))
        self.sub_langs_edit.setEnabled(False)
        self.sub_langs_edit.editingFinished.connect(self._on_option_changed)
        subs_row.addWidget(self.sub_langs_edit)
        subs_row.addStretch()
        layout.addLayout(subs_row)

        self.embed_thumbnail_checkbox = QCheckBox(self.tr("Embed thumbnail"))
        self.embed_thumbnail_checkbox.toggled.connect(self._on_option_changed)
        layout.addWidget(self.embed_thumbnail_checkbox)

        self.embed_metadata_checkbox = QCheckBox(self.tr("Embed title and metadata"))
        self.embed_metadata_checkbox.toggled.connect(self._on_option_changed)
        layout.addWidget(self.embed_metadata_checkbox)

        group.setLayout(layout)
        return group

    def _on_write_subs_toggled(self, checked: bool) -> None:
        self.sub_langs_edit.setEnabled(checked)

    def _create_output_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Output")))

        out_row = QHBoxLayout()
        out_row.setSpacing(SPACING.xs)
        out_row.addWidget(QLabel(self.tr("Output:")))

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

        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6)."""
        self.download_button = ModernButton(self.tr("Download"), variant="primary")
        self.download_button.clicked.connect(self._on_download)
        self._primary_button = self.download_button

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine / availability state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        """Probe yt-dlp availability off-thread, then update the Download guard."""
        config = self.config
        self.download_button.setEnabled(False)
        if self._suppress_optional_startup:
            return

        def _on_error(message: str) -> None:
            logger.warning("yt-dlp availability probe failed: %s", message)
            self._apply_probe_result(False)

        self._run_availability_scan(lambda: self._compute_ytdlp_available(config), self._apply_probe_result, _on_error)

    def _apply_probe_result(self, result: object) -> None:
        """Apply an availability-probe outcome, never enabling Download mid-run.

        A probe scheduled before a download started can land after it did —
        the button is pinned disabled for the run's duration regardless of
        what this probe found.
        """
        self._ytdlp_is_available = bool(result)
        self.engine_notice_label.setVisible(not self._ytdlp_is_available)
        self.download_button.setEnabled(self._ytdlp_is_available and not still_running(self.worker_thread))

    def _ytdlp_ready(self) -> bool:
        """Return the cached yt-dlp availability (probed once per config)."""
        return self._ytdlp_is_available

    @staticmethod
    def _compute_ytdlp_available(config: AnkiMinerConfig) -> bool:
        """Probe whether a usable yt-dlp executable is reachable for *config*.

        Runs the resolver (managed-binary re-hash included). Called only from
        ``__init__`` and ``update_config`` — readers use the cached bool via
        :meth:`_ytdlp_ready`.
        """
        return ytdlp_available(config)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_download(self) -> None:
        """Validate then start the DownloadWorker."""
        if not self._ytdlp_ready():
            # Should not happen (button disabled), but guard anyway.
            return

        # Reentrancy guard: never reassign self.worker_thread over a live thread.
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        # A fresh attempt supersedes the complaint about the last one (D24).
        self.clear_screen_issue()

        self.log_widget.clear_log()
        self.progress_widget.reset()

        urls = self._collect_urls()
        if not urls:
            return

        dest = self._custom_output_dir or self._default_download_dir
        # Pre-run writable check against the nearest existing directory (the
        # worker mkdir-s the destination itself).
        check_dir = dest if dest.exists() else dest.parent
        if not os.access(check_dir, os.W_OK):
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Download folder is not writable."),
                    details=tr_format(self.tr("Check permissions for %1."), str(dest)),
                )
            )
            return

        self._begin_tool_run(len(urls))
        self._total_urls = len(urls)
        self._run_urls = urls

        worker = DownloadWorker(
            self.config,
            urls,
            dest_dir=dest,
            options=self._build_options(),
        )
        self.worker_thread = worker

        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        worker.error.connect(self._on_run_error)
        # Lifecycle: free the QThread on real thread exit (see CondenseTab).
        worker.finished.connect(self._on_worker_finished)

        self.download_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    def _collect_urls(self) -> list[str]:
        """Return the validated URL list, or [] after raising a screen issue."""
        urls: list[str] = []
        bad: list[str] = []
        for raw_line in self.url_input.toPlainText().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = urlsplit(line)
            # http(s) only, and never a '-'-leading token that yt-dlp could
            # parse as an option (T-34 belt-and-braces; the command also uses
            # the '--' separator).
            if parts.scheme in ("http", "https") and parts.netloc and not line.startswith("-"):
                urls.append(line)
            else:
                bad.append(line)
        if bad:
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Some lines are not valid URLs."),
                    details="\n".join(bad),
                )
            )
            return []
        if not urls:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Paste at least one URL to download.")))
            return []
        return urls

    def _build_options(self) -> DownloadOptions:
        """Map the option widgets to DownloadOptions.

        A non-empty custom format string replaces the preset entirely,
        including audio extraction — raw mode, the user controls everything.
        """
        custom = self.custom_format_edit.text().strip()
        if custom:
            selector, audio_format = custom, None
        else:
            key = str(self.preset_combo.currentData())
            selector, audio_format = FORMAT_PRESETS.get(key, FORMAT_PRESETS["best"])
        return DownloadOptions(
            format_selector=selector,
            extract_audio_format=audio_format,
            write_subtitles=self.write_subs_checkbox.isChecked(),
            subtitle_langs=self.sub_langs_edit.text().strip() or "ja",
            embed_thumbnail=self.embed_thumbnail_checkbox.isChecked(),
            embed_metadata=self.embed_metadata_checkbox.isChecked(),
        )

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(tr_format(self.tr("Downloading %1 of %2"), str(idx + 1), str(self._total_urls)))
        if 0 <= idx < len(self._run_urls):
            self.log_widget.append_info(redact_url_for_log(self._run_urls[idx]))

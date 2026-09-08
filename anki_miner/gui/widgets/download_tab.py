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
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import QStandardPaths, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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
from anki_miner.gui.utils.language_names import (
    COMMON_SUBTITLE_LANGS,
    language_display_name,
    parse_lang_list,
)
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout
from anki_miner.gui.widgets.dialogs.language_picker_dialog import LanguagePickerDialog
from anki_miner.gui.widgets.dialogs.playlist_picker_dialog import PlaylistPickerDialog
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.gui.workers.download_probe_worker import (
    DownloadPlaylistResolveWorker,
    DownloadTracksProbeWorker,
)
from anki_miner.gui.workers.download_worker import DownloadWorker
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.services.media_downloader import (
    FORMAT_PRESETS,
    PLAYLIST_PROBE_MAX,
    DownloadOptions,
    DownloadPlaylist,
    MediaDownloaderService,
    UrlTracks,
)
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
        # The raw --sub-langs value. Held here rather than read off a widget:
        # the picker can return a yt-dlp expression no combo could represent.
        self._sub_langs: str = config.downloader_subtitle_langs
        # Last successful track probe, offered to the subtitle picker so it can
        # show what this URL really carries. Session-only.
        self._detected: UrlTracks | None = None
        self._probe_worker: DownloadTracksProbeWorker | None = None
        self._playlist_worker: DownloadPlaylistResolveWorker | None = None
        # The URL text of the line the in-flight resolve will replace. Text,
        # not an index: the user can edit the box while the probe runs.
        self._pending_playlist_url: str = ""
        # Playlist URL -> next entry position to fetch. A URL is present only
        # while its last batch was truncated; session-only.
        self._playlist_cursor: dict[str, int] = {}
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
            self._sub_langs = self.config.downloader_subtitle_langs
            self._refresh_sub_langs_button()
            self.sub_langs_button.setEnabled(self.config.downloader_write_subtitles)
            audio_idx = self.audio_lang_combo.findData(self.config.downloader_audio_lang)
            self.audio_lang_combo.setCurrentIndex(audio_idx if audio_idx >= 0 else 0)
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
            or self.config.downloader_subtitle_langs != self._normalized_sub_langs()
            or self.config.downloader_audio_lang != self._selected_audio_lang()
            or self.config.downloader_embed_thumbnail != self.embed_thumbnail_checkbox.isChecked()
            or self.config.downloader_embed_metadata != self.embed_metadata_checkbox.isChecked()
        )

    def _normalized_sub_langs(self) -> str:
        """The committed ``--sub-langs`` value.

        The single definition of the empty-selection fallback, which used to be
        duplicated across the diff, the persist slot and the options builder.
        ``--sub-langs ""`` is not a valid invocation, so an empty selection
        cannot reach yt-dlp.
        """
        return self._sub_langs.strip() or "ja"

    def _selected_audio_lang(self) -> str:
        return str(self.audio_lang_combo.currentData() or "")

    def _on_option_changed(self, *_: object) -> None:
        """Persist an edited run option to config so it survives restart."""
        if self._seeding:
            return
        new_config = replace(
            self.config,
            downloader_format_preset=str(self.preset_combo.currentData()),
            downloader_custom_format=self.custom_format_edit.text().strip(),
            downloader_write_subtitles=self.write_subs_checkbox.isChecked(),
            downloader_subtitle_langs=self._normalized_sub_langs(),
            downloader_audio_lang=self._selected_audio_lang(),
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
        self.url_input.textChanged.connect(self._refresh_url_actions)
        layout.addWidget(self.url_input)

        url_actions = QHBoxLayout()
        url_actions.setSpacing(SPACING.xs)
        self.expand_playlist_button = ModernButton(self.tr("Expand Playlist…"), variant="secondary")
        self.expand_playlist_button.setToolTip(
            self.tr(
                "Replace the URL line the cursor is on with the playlist's "
                "individual videos, so you can pick which ones to download."
            )
        )
        self.expand_playlist_button.clicked.connect(self._on_expand_playlist)
        url_actions.addWidget(self.expand_playlist_button)

        self.detect_button = ModernButton(self.tr("Detect Tracks"), variant="secondary")
        self.detect_button.setToolTip(self.tr("Ask the first URL which subtitle and audio languages it offers."))
        self.detect_button.clicked.connect(self._on_detect_tracks)
        url_actions.addWidget(self.detect_button)
        url_actions.addStretch()
        layout.addLayout(url_actions)

        group.setLayout(layout)
        self._refresh_url_actions()
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
        self.sub_langs_button = ModernButton(self.tr("Choose…"), variant="secondary")
        self.sub_langs_button.setToolTip(self.tr("Pick subtitle languages by name."))
        self.sub_langs_button.setEnabled(False)
        self.sub_langs_button.clicked.connect(self._on_choose_sub_langs)
        subs_row.addWidget(self.sub_langs_button)
        subs_row.addStretch()
        layout.addLayout(subs_row)

        audio_row = QHBoxLayout()
        audio_row.setSpacing(SPACING.xs)
        audio_row.addWidget(QLabel(self.tr("Audio language:")))
        self.audio_lang_combo = QComboBox()
        self.audio_lang_combo.setToolTip(
            self.tr(
                "Preferred audio track on videos that carry several. A video "
                "without this language still downloads, with its default audio."
            )
        )
        self.audio_lang_combo.addItem(self.tr("Any (best available)"), "")
        for code in COMMON_SUBTITLE_LANGS:
            self.audio_lang_combo.addItem(f"{language_display_name(code)}  ({code})", code)
        self.audio_lang_combo.currentIndexChanged.connect(self._on_option_changed)
        audio_row.addWidget(self.audio_lang_combo)
        audio_row.addStretch()
        layout.addLayout(audio_row)

        self.embed_thumbnail_checkbox = QCheckBox(self.tr("Embed thumbnail"))
        self.embed_thumbnail_checkbox.toggled.connect(self._on_option_changed)
        layout.addWidget(self.embed_thumbnail_checkbox)

        self.embed_metadata_checkbox = QCheckBox(self.tr("Embed title and metadata"))
        self.embed_metadata_checkbox.toggled.connect(self._on_option_changed)
        layout.addWidget(self.embed_metadata_checkbox)

        group.setLayout(layout)
        return group

    def _on_write_subs_toggled(self, checked: bool) -> None:
        self.sub_langs_button.setEnabled(checked)

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
            subtitle_langs=self._normalized_sub_langs(),
            # Raw mode owns the whole selector, audio included: composing a
            # language filter into a string the user hand-wrote would break the
            # contract the custom-format field advertises.
            audio_lang="" if custom else self._selected_audio_lang(),
            embed_thumbnail=self.embed_thumbnail_checkbox.isChecked(),
            embed_metadata=self.embed_metadata_checkbox.isChecked(),
        )

    # ------------------------------------------------------------------
    # Language pickers
    # ------------------------------------------------------------------

    def _refresh_sub_langs_button(self) -> None:
        """Show the current selection as names, or verbatim if it is raw."""
        value = self._normalized_sub_langs()
        codes = parse_lang_list(value)
        if codes is None:
            # A yt-dlp expression has no name; show what the user actually set.
            self.sub_langs_button.setText(value)
            return
        self.sub_langs_button.setText(", ".join(language_display_name(code) for code in codes))

    def _set_sub_langs(self, value: str) -> None:
        """Adopt a new ``--sub-langs`` value and persist it."""
        self._sub_langs = value
        self._refresh_sub_langs_button()
        self._on_option_changed()

    def _on_choose_sub_langs(self) -> None:
        """Open the language picker, seeded with the current value."""
        dialog = LanguagePickerDialog(self._sub_langs, detected=self._detected, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_sub_langs(dialog.selected_langs())

    # ------------------------------------------------------------------
    # Track detection
    # ------------------------------------------------------------------

    def _refresh_url_actions(self) -> None:
        """Enable the two URL-scoped actions only when they can do something."""
        has_url = bool(self._valid_urls())
        idle = not still_running(self.worker_thread)
        ready = self._ytdlp_ready() and has_url and idle
        self.detect_button.setEnabled(ready and not still_running(self._probe_worker))
        self.expand_playlist_button.setEnabled(ready and not still_running(self._playlist_worker))

    def _valid_urls(self) -> list[str]:
        """Every http(s) line in the box, without raising a screen issue."""
        urls: list[str] = []
        for raw_line in self.url_input.toPlainText().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("-"):
                continue
            parts = urlsplit(line)
            if parts.scheme in ("http", "https") and parts.netloc:
                urls.append(line)
        return urls

    def _on_detect_tracks(self) -> None:
        """Probe the first URL for the languages it actually offers."""
        urls = self._valid_urls()
        if not urls or still_running(self._probe_worker):
            return
        self.clear_screen_issue()
        self.detect_button.setEnabled(False)
        self.log_widget.append_info(self.tr("Checking available tracks…"))
        worker = DownloadTracksProbeWorker(MediaDownloaderService(self.config), urls[0], parent=self)
        # The URL rides along so a result for a line edited away mid-probe is dropped.
        worker.tracks_probed.connect(lambda tracks, url=urls[0]: self._on_tracks_probed_for(url, tracks))
        worker.probe_error.connect(self._on_tracks_error)
        worker.finished.connect(self._on_probe_finished)
        self._probe_worker = worker
        worker.start()

    def _on_tracks_probed_for(self, url: str, tracks: object) -> None:
        """Adopt a probe result only while its URL is still in the box."""
        if url not in self._valid_urls():
            logger.info("track probe result dropped: its URL is no longer in the box")
            self.log_widget.append_info(
                self.tr("The URL that was checked is no longer in the list; its tracks were ignored.")
            )
            self._refresh_url_actions()
            return
        self._on_tracks_probed(tracks)

    def _on_tracks_probed(self, tracks: object) -> None:
        """Adopt a probe result: remember it, and offer its audio languages."""
        if not isinstance(tracks, UrlTracks):  # pragma: no cover - signal guard
            return
        self._detected = tracks
        for code in tracks.audio_langs:
            if self.audio_lang_combo.findData(code) < 0:
                label = f"{language_display_name(code)}  ({code})" + self.tr("  · on this URL")
                # Seeding guard: adding an item must not look like the user
                # picking one, which would persist a config change.
                self._seeding = True
                try:
                    self.audio_lang_combo.addItem(label, code)
                finally:
                    self._seeding = False
        self.log_widget.append_success(
            tr_format(
                self.tr("Tracks found — subtitles: %1; audio: %2"),
                ", ".join(tracks.manual_sub_langs + tracks.auto_sub_langs) or self.tr("none"),
                ", ".join(tracks.audio_langs) or self.tr("none"),
            )
        )
        self._refresh_url_actions()

    def _on_tracks_error(self, message: str) -> None:
        """Report a failed track probe without blocking the download."""
        self.show_screen_issue(ScreenIssue(summary=self.tr("Could not read this URL's tracks."), details=message))
        self._refresh_url_actions()

    def _on_probe_finished(self) -> None:
        """Drop the probe handle once its QThread emits finished."""
        worker = self._probe_worker
        self._probe_worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_url_actions()

    # ------------------------------------------------------------------
    # Playlist expansion
    # ------------------------------------------------------------------

    def _on_expand_playlist(self) -> None:
        """Resolve the playlist on the cursor's line into its entries.

        The cursor's line, not "the only URL": expanding one playlist fills the
        box with its videos, and a second playlist must still be expandable
        afterwards.
        """
        if still_running(self._playlist_worker):
            return
        line_index = self.url_input.textCursor().blockNumber()
        lines = self.url_input.toPlainText().splitlines()
        candidate = lines[line_index].strip() if 0 <= line_index < len(lines) else ""
        parts = urlsplit(candidate) if candidate else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Put the cursor on the playlist URL line."),
                    details=self.tr("Expand Playlist works on the line the text cursor is on."),
                )
            )
            return

        self.clear_screen_issue()
        self._pending_playlist_url = candidate
        self.expand_playlist_button.setEnabled(False)
        self.log_widget.append_info(self.tr("Resolving playlist…"))
        worker = DownloadPlaylistResolveWorker(
            MediaDownloaderService(self.config),
            candidate,
            PLAYLIST_PROBE_MAX,
            start=self._playlist_cursor.get(candidate, 1),
            parent=self,
        )
        worker.playlist_resolved.connect(self._on_playlist_resolved)
        worker.probe_error.connect(self._on_playlist_error)
        worker.finished.connect(self._on_playlist_finished)
        self._playlist_worker = worker
        worker.start()

    def _on_playlist_resolved(self, playlist: object) -> None:
        """Let the user pick entries, then write them into the URL box.

        A truncated batch remembers where the next one starts, keyed by the
        playlist URL: pasting the URL again and expanding it continues instead
        of showing the same first page. The line itself is replaced as always —
        a bare playlist URL left in the box would download the whole playlist
        (``--no-playlist`` only applies to a URL that names a video AND a list).
        """
        if not isinstance(playlist, DownloadPlaylist):  # pragma: no cover - signal guard
            return
        url = self._pending_playlist_url
        lines = [line.strip() for line in self.url_input.toPlainText().splitlines()]
        if url not in lines:
            logger.info("playlist line was removed during the resolve; nothing added")
            self.log_widget.append_info(self.tr("Playlist line was removed; nothing added."))
            self._refresh_url_actions()
            return
        line_index = lines.index(url)
        dialog = PlaylistPickerDialog(playlist, truncated=playlist.truncated, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.log_widget.append_info(self.tr("Playlist expansion cancelled."))
            return
        chosen = dialog.selected_urls()
        added = self._replace_line(line_index, chosen)
        skipped = len(chosen) - added
        self.log_widget.append_success(tr_format(self.tr("Added %1 videos from '%2'."), str(added), playlist.title))
        if skipped:
            self.log_widget.append_info(tr_format(self.tr("Skipped %1 already in the list."), str(skipped)))
        if playlist.truncated:
            # Advance by the RAW window the probe asked for (limit positions),
            # not by the usable count: a private entry inside the window must
            # not make the next page re-fetch positions already seen.
            next_start = self._playlist_cursor.get(url, 1) + PLAYLIST_PROBE_MAX
            self._playlist_cursor[url] = next_start
            self.log_widget.append_info(
                tr_format(self.tr("Paste the playlist URL again and expand it for videos %1 onward."), str(next_start))
            )
        else:
            self._playlist_cursor.pop(url, None)
        self._refresh_url_actions()

    def _replace_line(self, line_index: int, urls: list[str]) -> int:
        """Swap one URL line for *urls*, dropping any already in the box.

        Returns the number of lines actually inserted. Rewriting the whole text
        rather than editing through a QTextCursor keeps the dedup rule in one
        readable place; the box holds at most a few hundred short lines.
        """
        lines = self.url_input.toPlainText().splitlines()
        if not 0 <= line_index < len(lines):
            return 0
        others = {line.strip() for i, line in enumerate(lines) if i != line_index and line.strip()}
        fresh = [url for url in urls if url not in others]
        lines[line_index : line_index + 1] = fresh
        self.url_input.setPlainText("\n".join(line for line in lines if line.strip()))
        return len(fresh)

    def _on_playlist_error(self, message: str) -> None:
        """Report a failed resolve — most often "that URL is not a playlist"."""
        # A page past the end ("The playlist is empty.") or any other failure
        # forgets the cursor, so the next expansion of this URL starts over.
        self._playlist_cursor.pop(self._pending_playlist_url, None)
        self.show_screen_issue(ScreenIssue(summary=self.tr("Could not expand that playlist."), details=message))
        self._refresh_url_actions()

    def _on_playlist_finished(self) -> None:
        """Drop the resolve handle once its QThread emits finished."""
        worker = self._playlist_worker
        self._playlist_worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_url_actions()

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        """Yield the base's workers plus this tab's two probe threads."""
        yield from super().iter_close_workers()
        for worker in (self._probe_worker, self._playlist_worker):
            if still_running(worker):
                assert worker is not None
                yield worker

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(tr_format(self.tr("Downloading %1 of %2"), str(idx + 1), str(self._total_urls)))
        if 0 <= idx < len(self._run_urls):
            self.log_widget.append_info(redact_url_for_log(self._run_urls[idx]))

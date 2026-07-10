"""Audio Condenser tab — condense media to dialogue-only audio.

Composes a single-file / folder mode toggle (a media
:class:`~anki_miner.gui.widgets.enhanced.FileSelector` plus an optional subtitle
selector per mode), single-mode audio- and subtitle-track override rows, inline
run options (padding, offset, output format, condensed-subs toggle), an
output-location row with an Overwrite checkbox, a Condense button, a
:class:`~anki_miner.gui.widgets.progress_widget.ProgressWidget` for overall queue
progress, and a :class:`~anki_miner.gui.widgets.log_widget.LogWidget` for per-file
pass/fail lines.

Structure and idioms are cloned from
:mod:`anki_miner.gui.widgets.subtitle_retime_tab` (mode toggle, off-thread
track probing, output-location row, worker lifecycle) — this tab is a sibling.

Guard contract:
- ffmpeg/ffprobe not found → Condense disabled, notice visible.
- Output directory not writable → Condense aborts, error logged.

Worker contract:
- Worker stored on ``self.worker_thread``.
- ``iter_close_workers()`` yields the active worker for
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Collection, Iterator, cast

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.dialogs import AudioTracksDialog, SubtitleTracksDialog
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.condense_worker import CondenseItem, CondenseWorker
from anki_miner.utils import list_audio_streams
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES, list_subtitle_streams
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream

logger = logging.getLogger(__name__)

# Condenser-local media set (D12): the mining VIDEO_EXTENSIONS plus audio-only
# containers. Kept here — NOT in gui/constants.py — so the condenser can accept
# audio inputs and ``.vtt`` subtitles without changing mining behavior.
CONDENSE_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".avi", ".m4v", ".mov"})
CONDENSE_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".opus", ".ogg", ".wav"}
)
CONDENSE_MEDIA_EXTENSIONS: frozenset[str] = CONDENSE_VIDEO_EXTENSIONS | CONDENSE_AUDIO_EXTENSIONS
CONDENSE_SUBTITLE_EXTENSIONS: tuple[str, ...] = (".ass", ".ssa", ".srt", ".vtt")


def _build_filter(label: str, extensions: Collection[str]) -> str:
    """Return a Qt file-dialog filter string for *extensions* (condenser-local)."""
    patterns = " ".join(f"*{ext}" for ext in sorted(extensions))
    return f"{label} ({patterns});;All Files (*)"


CONDENSE_MEDIA_FILE_FILTER = _build_filter("Media Files", CONDENSE_MEDIA_EXTENSIONS)
CONDENSE_SUBTITLE_FILE_FILTER = _build_filter("Subtitle Files", CONDENSE_SUBTITLE_EXTENSIONS)

# Option widget ranges.
_PADDING_MIN = 0
_PADDING_MAX = 10_000
_PADDING_STEP = 50
_OFFSET_MIN = -600_000
_OFFSET_MAX = 600_000
_OFFSET_STEP = 100


class CondenseTab(QWidget):
    """Tab for condensing media files to dialogue-only audio.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.

    Signals:
        config_changed: Emitted with a new ``AnkiMinerConfig`` when the user
            edits a run option (padding / offset / format / write-subs), so the
            host can persist ``condenser_*`` to ``gui_config.json`` and survive
            restart. Mirrors ``SettingsTab.config_changed`` → ``update_config``.
    """

    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        # Suppresses the persist slot while _apply_config_defaults programmatically
        # seeds the option widgets (setValue/setChecked would otherwise feed back
        # through config_changed and re-persist during a refresh).
        self._seeding: bool = False
        self.worker_thread: CondenseWorker | None = None
        self._custom_output_dir: Path | None = None
        self._total_files: int = 0
        self._cancelled: bool = False
        # Per-run track selections for single-file mode; reset when the media
        # file changes (a pick must not silently carry over to a different file).
        self._audio_track_override: int | None = None
        self._subtitle_track_override: int | None = None
        # ffmpeg availability is cached per-config: probing it (resolve_ffmpeg /
        # resolve_ffprobe + shutil.which / Path.exists) is a PATH scan we must
        # not repeat on every read. Recomputed only here and in update_config().
        self._ffmpeg_is_available: bool = self._compute_ffmpeg_available()

        self._setup_ui()
        self._apply_config_defaults()
        self._refresh_engine_state()

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new application config (e.g. after the ffmpeg path changes).

        A config change is exactly when ffmpeg can appear/disappear, so the
        availability cache is recomputed here. Option-widget defaults are
        re-seeded only when idle AND only when the new config's condenser_*
        values actually differ from the live widgets: a run already in flight
        captured its own values, and an unrelated refresh (e.g. a header theme
        toggle) must not stomp what the user typed but hasn't yet committed.
        """
        self.config = config
        self._ffmpeg_is_available = self._compute_ffmpeg_available()
        idle = self.worker_thread is None or not self.worker_thread.isRunning()
        if idle and self._options_differ_from_widgets():
            self._apply_config_defaults()
        self._refresh_engine_state()

    def _apply_config_defaults(self) -> None:
        """Seed the option widgets from the current config's persisted defaults.

        Guarded by ``_seeding`` so the programmatic setValue/setChecked calls
        don't feed back through ``_on_option_changed`` and re-emit config_changed.
        """
        self._seeding = True
        try:
            self.padding_spinbox.setValue(self.config.condenser_padding_ms)
            self.offset_spinbox.setValue(self.config.condenser_offset_ms)
            idx = self.format_combo.findData(self.config.condenser_output_format)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
            self.write_subs_checkbox.setChecked(self.config.condenser_write_subtitles)
        finally:
            self._seeding = False

    def _options_differ_from_widgets(self) -> bool:
        """Whether the config's condenser_* values differ from the live widgets."""
        return (
            self.config.condenser_padding_ms != self.padding_spinbox.value()
            or self.config.condenser_offset_ms != self.offset_spinbox.value()
            or self.config.condenser_output_format != self.format_combo.currentData()
            or self.config.condenser_write_subtitles != self.write_subs_checkbox.isChecked()
        )

    def _on_option_changed(self, *_: object) -> None:
        """Persist an edited run option to config so it survives restart.

        Folds all four widgets into a fresh config and emits ``config_changed``
        for the host to save. No-ops during programmatic seeding and when
        nothing actually changed (guards against a save/refresh feedback loop).
        """
        if self._seeding:
            return
        new_config = replace(
            self.config,
            condenser_padding_ms=self.padding_spinbox.value(),
            condenser_offset_ms=self.offset_spinbox.value(),
            condenser_output_format=self.format_combo.currentData(),
            condenser_write_subtitles=self.write_subs_checkbox.isChecked(),
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
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(self._create_input_section())
        layout.addWidget(self._create_options_section())
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

        # ffmpeg notice (shown when ffmpeg/ffprobe unavailable)
        self.engine_notice_label = QLabel(
            self.tr("ffmpeg not found; install it or set its path in Settings to enable condensing.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        input_desc = QLabel(self.tr("Condense a video or audio file down to just its spoken dialogue."))
        input_desc.setObjectName("helper-text")
        input_desc.setWordWrap(True)
        layout.addWidget(input_desc)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_row.addWidget(QLabel(self.tr("Mode:")))

        self.file_mode_button = ModernButton(self.tr("Single File"), variant="primary")
        self.file_mode_button.setCheckable(True)
        self.file_mode_button.setChecked(True)
        self.file_mode_button.setToolTip(self.tr("Condense one selected media file."))
        self.file_mode_button.clicked.connect(self._on_file_mode)
        mode_row.addWidget(self.file_mode_button)

        self.folder_mode_button = ModernButton(self.tr("Folder"), variant="secondary")
        self.folder_mode_button.setCheckable(True)
        self.folder_mode_button.setChecked(False)
        self.folder_mode_button.setToolTip(self.tr("Condense every media file in a selected folder."))
        self.folder_mode_button.clicked.connect(self._on_folder_mode)
        mode_row.addWidget(self.folder_mode_button)

        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Single-mode selectors
        self.media_file_selector = FileSelector(
            label=self.tr("Media File:"),
            file_mode=True,
            file_filter=CONDENSE_MEDIA_FILE_FILTER,
        )
        layout.addWidget(self.media_file_selector)

        self.subtitle_file_selector = FileSelector(
            label=self.tr("Subtitle File:"),
            file_mode=True,
            file_filter=CONDENSE_SUBTITLE_FILE_FILTER,
        )
        layout.addWidget(self.subtitle_file_selector)

        subtitle_hint = QLabel(self.tr("Leave empty to auto-detect (sibling file or embedded track)."))
        subtitle_hint.setObjectName("helper-text")
        subtitle_hint.setWordWrap(True)
        layout.addWidget(subtitle_hint)

        # Reset the track overrides whenever the media file changes.
        self.media_file_selector.path_changed.connect(self._on_media_path_changed)
        # Picking an explicit subtitle file makes the embedded-track override
        # meaningless, so its row is disabled while a file is selected.
        self.subtitle_file_selector.path_changed.connect(self._on_subtitle_path_changed)

        # Single-mode audio-track override row.
        self.audio_track_row_widget = QWidget()
        audio_row = QHBoxLayout(self.audio_track_row_widget)
        audio_row.setContentsMargins(0, 0, 0, 0)
        audio_row.setSpacing(SPACING.xs)
        audio_row.addWidget(QLabel(self.tr("Audio track:")))
        self.audio_track_label = QLabel(self.tr("Japanese (auto-detect)"))
        self.audio_track_label.setObjectName("output-location-value")
        audio_row.addWidget(self.audio_track_label, 1)
        self.audio_tracks_button = ModernButton(self.tr("Choose…"), variant="secondary")
        self.audio_tracks_button.setToolTip(self.tr("Choose which audio track to condense."))
        self.audio_tracks_button.clicked.connect(self._on_audio_tracks_clicked)
        audio_row.addWidget(self.audio_tracks_button)
        layout.addWidget(self.audio_track_row_widget)

        # Single-mode subtitle-track override row.
        self.subtitle_track_row_widget = QWidget()
        sub_row = QHBoxLayout(self.subtitle_track_row_widget)
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(SPACING.xs)
        sub_row.addWidget(QLabel(self.tr("Subtitle track:")))
        self.subtitle_track_label = QLabel(self.tr("Auto (external → embedded Japanese)"))
        self.subtitle_track_label.setObjectName("output-location-value")
        sub_row.addWidget(self.subtitle_track_label, 1)
        self.subtitle_tracks_button = ModernButton(self.tr("Choose…"), variant="secondary")
        self.subtitle_tracks_button.setToolTip(self.tr("Choose which embedded subtitle track to condense against."))
        self.subtitle_tracks_button.clicked.connect(self._on_subtitle_tracks_clicked)
        sub_row.addWidget(self.subtitle_tracks_button)
        layout.addWidget(self.subtitle_track_row_widget)

        # Folder-mode selectors (hidden by default)
        self.media_folder_selector = FileSelector(
            label=self.tr("Media Folder:"),
            file_mode=False,
        )
        self.media_folder_selector.hide()
        layout.addWidget(self.media_folder_selector)

        self.subtitle_folder_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
        )
        self.subtitle_folder_selector.hide()
        layout.addWidget(self.subtitle_folder_selector)

        subfolder_hint = QLabel(
            self.tr(
                "Optional. When set, media is paired to subtitles by episode number; otherwise each file auto-detects."
            )
        )
        subfolder_hint.setObjectName("helper-text")
        subfolder_hint.setWordWrap(True)
        subfolder_hint.hide()
        self.subtitle_folder_hint = subfolder_hint
        layout.addWidget(subfolder_hint)

        group.setLayout(layout)
        return group

    def _create_options_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Options")))

        # Padding
        padding_row = QHBoxLayout()
        padding_row.setSpacing(SPACING.xs)
        padding_row.addWidget(QLabel(self.tr("Padding:")))
        self.padding_spinbox = QSpinBox()
        self.padding_spinbox.setRange(_PADDING_MIN, _PADDING_MAX)
        self.padding_spinbox.setSingleStep(_PADDING_STEP)
        self.padding_spinbox.setSuffix(self.tr(" ms"))
        self.padding_spinbox.setToolTip(self.tr("Silence kept on each side of every dialogue line before merging."))
        padding_row.addWidget(self.padding_spinbox)
        padding_row.addStretch()
        layout.addLayout(padding_row)

        # Offset
        offset_row = QHBoxLayout()
        offset_row.setSpacing(SPACING.xs)
        offset_row.addWidget(QLabel(self.tr("Offset:")))
        self.offset_spinbox = QSpinBox()
        self.offset_spinbox.setRange(_OFFSET_MIN, _OFFSET_MAX)
        self.offset_spinbox.setSingleStep(_OFFSET_STEP)
        self.offset_spinbox.setSuffix(self.tr(" ms"))
        self.offset_spinbox.setToolTip(self.tr("Shift every subtitle cue by this amount before condensing."))
        offset_row.addWidget(self.offset_spinbox)
        offset_row.addStretch()
        layout.addLayout(offset_row)

        # Output format
        format_row = QHBoxLayout()
        format_row.setSpacing(SPACING.xs)
        format_row.addWidget(QLabel(self.tr("Format:")))
        self.format_combo = QComboBox()
        self.format_combo.addItem("MP3", "mp3")
        self.format_combo.addItem("Opus", "opus")
        self.format_combo.addItem("FLAC", "flac")
        format_row.addWidget(self.format_combo)
        format_row.addStretch()
        layout.addLayout(format_row)

        # Condensed-subtitle sidecars
        self.write_subs_checkbox = QCheckBox(self.tr("Also write condensed subtitles (SRT + LRC)"))
        self.write_subs_checkbox.setToolTip(
            self.tr("Write time-mapped .srt and .lrc files alongside the condensed audio.")
        )
        layout.addWidget(self.write_subs_checkbox)

        # Persist any run-option edit to config (survives restart). Guarded by
        # _seeding so _apply_config_defaults' programmatic writes don't re-emit.
        self.padding_spinbox.valueChanged.connect(self._on_option_changed)
        self.offset_spinbox.valueChanged.connect(self._on_option_changed)
        self.format_combo.currentIndexChanged.connect(self._on_option_changed)
        self.write_subs_checkbox.toggled.connect(self._on_option_changed)

        group.setLayout(layout)
        return group

    def _create_output_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self.tr("Output")))

        out_row = QHBoxLayout()
        out_row.setSpacing(SPACING.xs)
        out_row.addWidget(QLabel(self.tr("Output:")))

        self.output_location_label = QLabel(self.tr("Next to source"))
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

        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing condensed files"))
        self.overwrite_checkbox.setToolTip(
            self.tr("When unchecked, files whose condensed audio already exists are skipped, not overwritten.")
        )
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

        self.condense_button = ModernButton(self.tr("Condense Audio"), variant="primary")
        self.condense_button.clicked.connect(self._on_condense)
        btn_row.addWidget(self.condense_button)

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
        """Enable/disable Condense based on ffmpeg availability."""
        available = self._ffmpeg_available()
        self.engine_notice_label.setVisible(not available)
        self.condense_button.setEnabled(available)

    def _ffmpeg_available(self) -> bool:
        """Return the cached ffmpeg availability (probed once per config)."""
        return self._ffmpeg_is_available

    def _compute_ffmpeg_available(self) -> bool:
        """Probe whether both ffmpeg and ffprobe are reachable for the config.

        Runs the PATH scan (resolve_ffmpeg / resolve_ffprobe + shutil.which /
        Path.exists). Called only from ``__init__`` and ``update_config`` —
        readers use the cached bool via :meth:`_ffmpeg_available`.
        """
        return self._binary_available(resolve_ffmpeg(self.config)) and self._binary_available(
            resolve_ffprobe(self.config)
        )

    @staticmethod
    def _binary_available(resolved: str) -> bool:
        """Return whether *resolved* (a PATH literal or explicit path) is reachable."""
        if resolved in ("ffmpeg", "ffprobe"):
            # PATH fallback literal — check shutil.which.
            return shutil.which(resolved) is not None
        return Path(resolved).exists()

    # ------------------------------------------------------------------
    # Mode toggle slots
    # ------------------------------------------------------------------

    def _on_file_mode(self) -> None:
        self.file_mode_button.setChecked(True)
        self.folder_mode_button.setChecked(False)
        self.media_file_selector.show()
        self.subtitle_file_selector.show()
        self.audio_track_row_widget.show()
        self.subtitle_track_row_widget.show()
        self.media_folder_selector.hide()
        self.subtitle_folder_selector.hide()
        self.subtitle_folder_hint.hide()

    def _on_folder_mode(self) -> None:
        self.folder_mode_button.setChecked(True)
        self.file_mode_button.setChecked(False)
        self.media_file_selector.hide()
        self.subtitle_file_selector.hide()
        # Folder mode auto-detects the track per file; no per-file pick.
        self.audio_track_row_widget.hide()
        self.subtitle_track_row_widget.hide()
        self.media_folder_selector.show()
        self.subtitle_folder_selector.show()
        self.subtitle_folder_hint.show()

    # ------------------------------------------------------------------
    # Track selection (single-file mode)
    # ------------------------------------------------------------------

    def _on_media_path_changed(self, new_path: str) -> None:
        """Reset both track overrides when the media file changes."""
        self._audio_track_override = None
        self._subtitle_track_override = None
        self.audio_track_label.setText(self.tr("Japanese (auto-detect)"))
        self.subtitle_track_label.setText(self.tr("Auto (external → embedded Japanese)"))

    def _on_subtitle_path_changed(self, new_path: str) -> None:
        """Disable the embedded-subtitle-track row when an explicit sub is picked."""
        self.subtitle_track_row_widget.setEnabled(not new_path.strip())

    def _on_audio_tracks_clicked(self) -> None:
        """Open AudioTracksDialog to pick which audio track to condense."""
        media_path = self.media_file_selector.get_path().strip()
        if not media_path:
            QMessageBox.warning(self, self.tr("No Media File Selected"), self.tr("Select a media file first."))
            return
        media_file = Path(media_path)
        if not media_file.is_file():
            QMessageBox.warning(self, self.tr("File Not Found"), self.tr("Media file not found: ") + media_path)
            return

        ffprobe_cmd = resolve_ffprobe(self.config)
        # Probe off the GUI thread — ffprobe on a large file can block long
        # enough to freeze the UI. Disable the button so a second click can't
        # spawn a parallel probe; re-enabled in both callbacks.
        self.audio_tracks_button.setEnabled(False)

        def _probe() -> object:
            return list_audio_streams(media_file, ffprobe_cmd=ffprobe_cmd)

        def _on_streams(result: object) -> None:
            try:
                self.audio_tracks_button.setEnabled(True)
            except RuntimeError:
                # Tab torn down while the probe was in flight (its C++ button is
                # gone); the queued callback has nothing live to update.
                return
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
            logger.error("Failed to probe audio tracks: %s", msg)
            try:
                self.audio_tracks_button.setEnabled(True)
            except RuntimeError:
                return
            QMessageBox.warning(
                self,
                self.tr("Probe Failed"),
                self.tr("Failed to detect audio tracks. Check that ffprobe is installed."),
            )

        run_off_thread(self, _probe, _on_streams, _on_probe_error)

    def _on_subtitle_tracks_clicked(self) -> None:
        """Open SubtitleTracksDialog to pick which embedded subtitle track to use."""
        media_path = self.media_file_selector.get_path().strip()
        if not media_path:
            QMessageBox.warning(self, self.tr("No Media File Selected"), self.tr("Select a media file first."))
            return
        media_file = Path(media_path)
        if not media_file.is_file():
            QMessageBox.warning(self, self.tr("File Not Found"), self.tr("Media file not found: ") + media_path)
            return

        ffprobe_cmd = resolve_ffprobe(self.config)
        self.subtitle_tracks_button.setEnabled(False)

        def _probe() -> object:
            return list_subtitle_streams(media_file, ffprobe_cmd)

        def _on_streams(result: object) -> None:
            try:
                self.subtitle_tracks_button.setEnabled(True)
            except RuntimeError:
                return
            streams = cast("list[SubtitleStream]", result)
            if not streams:
                QMessageBox.information(
                    self,
                    self.tr("No Subtitle Tracks"),
                    self.tr("No embedded subtitle tracks detected in this file."),
                )
                return

            # Caller computes the auto-detected stream: the first JP-tagged text
            # track, else None (the worker then falls back to the first text track).
            auto_stream = next(
                (s for s in streams if s.is_text and s.language_tag in JAPANESE_LANGUAGE_CODES),
                None,
            )
            dialog = SubtitleTracksDialog(
                streams=streams,
                current_override=self._subtitle_track_override,
                auto_detected=auto_stream,
                parent=self,
            )
            if dialog.exec() == SubtitleTracksDialog.DialogCode.Accepted:
                self._subtitle_track_override = dialog.selected_override()
                if self._subtitle_track_override is None:
                    self.subtitle_track_label.setText(self.tr("Auto (external → embedded Japanese)"))
                else:
                    self.subtitle_track_label.setText(
                        tr_format(self.tr("Track %1"), str(self._subtitle_track_override + 1))
                    )

        def _on_probe_error(msg: str) -> None:
            logger.error("Failed to probe subtitle tracks: %s", msg)
            try:
                self.subtitle_tracks_button.setEnabled(True)
            except RuntimeError:
                return
            QMessageBox.warning(
                self,
                self.tr("Probe Failed"),
                self.tr("Failed to detect subtitle tracks. Check that ffprobe is installed."),
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
        self.output_location_label.setText(self.tr("Next to source"))
        self.clear_output_button.hide()

    # ------------------------------------------------------------------
    # Condense
    # ------------------------------------------------------------------

    def _on_condense(self) -> None:
        """Validate then start the CondenseWorker."""
        if not self._ffmpeg_available():
            # Should not happen (button disabled), but guard anyway.
            return

        # Reentrancy guard: a prior run's QThread may still be tearing down when
        # queue_finished re-enabled the button. Never reassign self.worker_thread
        # over a live thread.
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        # Clear the log before collecting: _collect_items logs the pairing
        # summary ("Matched N of M") we must not wipe afterwards.
        self.log_widget.clear_log()
        self.progress_widget.reset()

        items = self._collect_items()
        if not items:
            return

        out_dir = self._custom_output_dir

        # Pre-run writable check. When out_dir is None every output lands next to
        # its source media, so check the first item's parent.
        check_dir = out_dir if out_dir is not None else items[0].media.parent
        if not os.access(check_dir, os.W_OK):
            self.log_widget.append_error(self.tr("Output directory is not writable: ") + str(check_dir))
            return

        self._cancelled = False
        self._total_files = len(items)

        # Single-file mode honors the per-file track picks; folder mode auto-detects.
        single_mode = not self.media_file_selector.isHidden()
        audio_override = self._audio_track_override if single_mode else None
        subtitle_override = self._subtitle_track_override if single_mode else None

        worker = CondenseWorker(
            self.config,
            items,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
            padding_ms=self.padding_spinbox.value(),
            offset_ms=self.offset_spinbox.value(),
            output_format=self.format_combo.currentData(),
            bitrate_kbps=self.config.condenser_bitrate_kbps,
            filtered_chars=self.config.condenser_filtered_chars,
            write_subs=self.write_subs_checkbox.isChecked(),
            audio_track_override=audio_override,
            subtitle_track_override=subtitle_override,
        )
        self.worker_thread = worker

        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        # Lifecycle: free the QThread on real thread exit (not on queue_finished,
        # which fires just before the thread ends). Clears the handle so the
        # reentrancy guard and iter_close_workers see no stale worker.
        worker.finished.connect(self._on_worker_finished)

        self.condense_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    def _collect_items(self) -> list[CondenseItem]:
        """Return the ordered list of items to condense, or [] on validation failure."""
        if not self.media_file_selector.isHidden():
            return self._collect_single_item()
        return self._collect_folder_items()

    def _collect_single_item(self) -> list[CondenseItem]:
        media_str = self.media_file_selector.get_path().strip()
        sub_str = self.subtitle_file_selector.get_path().strip()

        if not media_str:
            QMessageBox.warning(
                self,
                self.tr("No Media File Selected"),
                self.tr("Select a media file before condensing."),
            )
            return []

        media = Path(media_str)
        if not media.is_file():
            QMessageBox.warning(
                self,
                self.tr("File Not Found"),
                self.tr("Media file not found: ") + media_str,
            )
            return []

        external_sub: Path | None = None
        if sub_str:
            sub = Path(sub_str)
            if not sub.is_file():
                QMessageBox.warning(
                    self,
                    self.tr("File Not Found"),
                    self.tr("Subtitle file not found: ") + sub_str,
                )
                return []
            external_sub = sub

        return [CondenseItem(media, external_sub)]

    def _collect_folder_items(self) -> list[CondenseItem]:
        media_folder_str = self.media_folder_selector.get_path().strip()
        sub_folder_str = self.subtitle_folder_selector.get_path().strip()

        if not media_folder_str:
            QMessageBox.warning(
                self,
                self.tr("No Media Folder Selected"),
                self.tr("Select a media folder before condensing."),
            )
            return []

        media_folder = Path(media_folder_str)
        if not media_folder.is_dir():
            QMessageBox.warning(
                self,
                self.tr("Folder Not Found"),
                self.tr("Media folder not found: ") + media_folder_str,
            )
            return []

        # Optional subtitle folder → episode-number pairing (condenser extension
        # sets, incl. audio inputs and .vtt).
        if sub_folder_str:
            sub_folder = Path(sub_folder_str)
            if not sub_folder.is_dir():
                QMessageBox.warning(
                    self,
                    self.tr("Folder Not Found"),
                    self.tr("Subtitle folder not found: ") + sub_folder_str,
                )
                return []
            return self._pair_folder_items(media_folder, sub_folder)

        # No subtitle folder → per-file auto-detection over the media folder.
        media_files = sorted(
            f for f in media_folder.iterdir() if f.is_file() and f.suffix.lower() in CONDENSE_MEDIA_EXTENSIONS
        )
        if not media_files:
            QMessageBox.warning(
                self,
                self.tr("No Media Files"),
                self.tr("No media files found in the selected folder."),
            )
            return []
        return [CondenseItem(m, None) for m in media_files]

    def _pair_folder_items(self, media_folder: Path, sub_folder: Path) -> list[CondenseItem]:
        all_media = sorted(
            f for f in media_folder.iterdir() if f.is_file() and f.suffix.lower() in CONDENSE_MEDIA_EXTENSIONS
        )
        total_media = len(all_media)

        file_pairs = FilePairMatcher.find_pairs_by_episode_number(
            media_folder,
            sub_folder,
            video_extensions=CONDENSE_MEDIA_EXTENSIONS,
            subtitle_extensions=CONDENSE_SUBTITLE_EXTENSIONS,
        )
        n_matched = len(file_pairs)

        self.log_widget.append_success(
            tr_format(self.tr("Matched %1 of %2 media files."), str(n_matched), str(total_media))
        )

        if n_matched < total_media:
            matched_media = {fp.video for fp in file_pairs}
            n_unmatched = sum(1 for m in all_media if m not in matched_media)
            self.log_widget.append_error(
                tr_format(self.tr("Warning: %1 media file(s) could not be matched."), str(n_unmatched))
            )

        if not file_pairs:
            QMessageBox.warning(
                self,
                self.tr("No Pairs Matched"),
                self.tr("No subtitle files could be matched to the media files in the selected folders."),
            )
            return []

        return [CondenseItem(fp.video, fp.subtitle) for fp in file_pairs]

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Condensing file %1 of %2"), str(idx + 1), str(self._total_files))
        )

    def _on_file_progress(self, idx: int, pct: int, message: str) -> None:
        # Compose the intra-file condense fraction into the whole-run bar so
        # long files show live movement, not a bar frozen per file.
        self.progress_widget.set_composed(idx, pct, self._total_files, message)

    def _on_file_finished(self, idx: int, out_path: object, error_str: object) -> None:
        # Whole-file advance in percent units (matches set_composed's ETA math).
        if self._total_files:
            self.progress_widget.set_percent(int((idx + 1) / self._total_files * 100))
        if error_str:
            self.log_widget.append_error(str(error_str))
        else:
            path_label = str(out_path) if out_path else ""
            self.log_widget.append_success(self.tr("Done: ") + Path(path_label).name if path_label else self.tr("Done"))

    def _on_file_skipped(self, idx: int, out_path: object) -> None:
        # Advance the progress bar just like a finished file.
        if self._total_files:
            self.progress_widget.set_percent(int((idx + 1) / self._total_files * 100))
        path_label = str(out_path) if out_path else ""
        self.log_widget.append_info(self.tr("Skipped: ") + Path(path_label).name if path_label else self.tr("Skipped"))

    def _on_queue_finished(self) -> None:
        self.condense_button.setEnabled(True)
        self.cancel_button.hide()
        # Reset for the next run's cancel button.
        self.cancel_button.setText(self.tr("Cancel"))
        self.cancel_button.setEnabled(True)
        if self._cancelled:
            self.progress_widget.reset()
            self.progress_widget.set_status(self.tr("Cancelled"))
        else:
            self.progress_widget.show_completion(tr_format(self.tr("Complete — %1 files processed"), self._total_files))

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

    def iter_close_workers(self) -> Iterator[CondenseWorker]:
        """Yield the active worker so BackgroundTaskController can join it on close."""
        if self.worker_thread is not None:
            yield self.worker_thread

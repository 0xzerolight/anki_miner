"""Tracks tab (Utilities → Tracks): save a video's embedded tracks as their own files.

Pick a video (.mkv, .mp4, …) or a folder of them; the tool lists the file's
subtitle and audio tracks, and Extract Tracks saves each ticked one by stream
copy (:mod:`anki_miner.services.track_extractor`). The video is never changed.
A lone ticked subtitle is named after its video, so mining pairs it like any
sibling subtitle.

Structure follows :mod:`anki_miner.gui.widgets.readability_tab` (one field takes
a file or a folder) plus Generate's Output row and Overwrite box.

Guard contract:
- ffmpeg or ffprobe missing → Extract disabled, notice visible, nothing listed.
- No path / missing path / list not loaded / nothing ticked / empty folder →
  screen issue, nothing starts.
- Listing the tracks is a probe: it never clears a screen issue (B-11).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QCheckBox, QFrame, QLabel, QScrollArea, QTableWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import (
    configure_data_view,
    configure_table_header,
    data_row_height,
    install_copy_rows,
    make_table_item,
)
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout, field_label_width
from anki_miner.gui.widgets.dialogs.audio_tracks_dialog import _format_channels
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.workers.track_extract_worker import TrackExtractWorker
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.reading._util import natural_sort_key
from anki_miner.services.track_extractor import (
    TRACKS_VIDEO_EXTENSIONS,
    InputProbe,
    TrackRef,
    is_track_input,
    probe_input,
    track_format,
)
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.i18n import tr_format

#: Typing a path fires path_changed per keystroke; list the tracks once it settles.
_PROBE_DEBOUNCE_MS = 300
#: Rows the table shows before it scrolls (a floor in rows, not pixels).
_MIN_VISIBLE_ROWS = 6
_NO_VALUE = "—"
#: Codec names a learner recognises; anything else shows ffprobe's name upper-cased.
_CODEC_LABELS = {
    "ass": "ASS",
    "ssa": "SSA",
    "subrip": "SRT",
    "webvtt": "WebVTT",
    "mov_text": "MP4 text",
    "hdmv_pgs_subtitle": "PGS",
    "dvd_subtitle": "VobSub",
    "dvb_subtitle": "DVB",
}


def _codec_label(codec: str | None) -> str:
    if not codec:
        return _NO_VALUE
    return _CODEC_LABELS.get(codec, codec.upper())


class TracksTab(_ToolTabBase):
    """List a video's tracks and save the ticked ones as files.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    PAGE_WIDTH = PageWidth.PAGE
    TASK_ID = "tools.tracks"
    TASK_OWNER = CapabilityTarget("subtitles", "tracks")
    OUTPUT_HISTORY_KEY = "tools.tracks.output"
    _PROBE_NAME = "ffmpeg"

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
        self._cancelled: bool = False
        self._engine_is_available: bool = False
        self._total_files: int = 0
        #: The listing on screen, or None while none is (no path, or one in flight).
        self._probe: InputProbe | None = None
        #: Bumped on every path change; a listing from an older path is dropped.
        self._probe_generation: int = 0
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
            partial=self.tr("Finished with errors — see log"),
            run_problem=self.tr("Some tracks could not be saved."),
            run_problem_single=self.tr("Some tracks of this video could not be saved."),
            complete_template=self.tr("Complete — tracks saved from %1 video(s)"),
            complete_skipped_template=self.tr("Complete — %1 video(s) done, %2 skipped"),
            all_skipped_template=self.tr("Nothing saved — every video was skipped. The log says why."),
            select_output_folder=self.tr("Select Output Folder"),
            output_default=self.tr("Next to each video"),
            task_title=self.tr("Track extraction"),
        )

        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(_PROBE_DEBOUNCE_MS)
        self._probe_timer.timeout.connect(self._probe_tracks)

        self._setup_ui()
        self.input_selector.path_changed.connect(self._on_input_changed)
        self._refresh_engine_state()

    def _item_total(self) -> int:
        return self._total_files

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new config; a run in flight keeps the one it captured."""
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

        layout.addWidget(self._create_input_card())
        layout.addWidget(self._create_tracks_card())
        layout.addWidget(self._create_output_card())
        self._create_action_buttons()
        layout.addWidget(self._create_progress_section())
        layout.addStretch()
        container.setLayout(layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._install_action_bar(main_layout, scroll_area, container, self.PAGE_WIDTH)
        self.setLayout(main_layout)
        self.install_issue_banner(main_layout)

    def _create_input_card(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)
        layout.addWidget(SectionHeader(self.tr("Video")))

        self.engine_notice_label = QLabel(
            self.tr("ffmpeg and ffprobe were not found. Install ffmpeg and add it to PATH to use this tool.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        desc = QLabel(
            self.tr(
                "Save the subtitle and audio tracks inside a video as their own files. "
                "Tracks are copied as they are; the video is never changed."
            )
        )
        desc.setObjectName("helper-text")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        patterns = " ".join(f"*{ext}" for ext in sorted(TRACKS_VIDEO_EXTENSIONS))
        file_filter = tr_format(self.tr("Videos (%1)"), patterns) + ";;" + self.tr("All Files (*)")
        wrong_kind = self.tr("This field takes a video or a folder.")
        self.input_selector = FileSelector(
            label=self.tr("Video file or folder:"),
            file_mode=True,
            allow_folder=True,
            file_filter=file_filter,
            label_width=field_label_width(self.tr("Video file or folder:")),
            history_key="tools.tracks.inputs",
            drop_validator=lambda p: (p.is_dir() or is_track_input(p), wrong_kind),
        )
        layout.addWidget(self.input_selector)
        group.setLayout(layout)
        return group

    def _create_tracks_card(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)
        layout.addWidget(SectionHeader(self.tr("Tracks")))

        self.tracks_status_label = QLabel(self.tr("Choose a video to list its tracks."))
        self.tracks_status_label.setObjectName("helper-text")
        self.tracks_status_label.setWordWrap(True)
        layout.addWidget(self.tracks_status_label)

        self.tracks_table = QTableWidget(0, 5)
        headers = (
            (self.tr("Track"), self.tr("Tick the tracks to save.")),
            (self.tr("Language"), self.tr("The language the file says the track is in.")),
            (self.tr("Codec"), self.tr("The track's format. Hover a row to see the file type it is saved as.")),
            (self.tr("Title"), self.tr("The track's name in the file, such as Signs & Songs.")),
            (self.tr("Flags"), self.tr("Default, Forced, and Image for picture-based subtitles.")),
        )
        self.tracks_table.setHorizontalHeaderLabels([label for label, _tip in headers])
        for col, (_label, tip) in enumerate(headers):
            item = self.tracks_table.horizontalHeaderItem(col)
            if item is not None:
                item.setToolTip(tip)
        configure_table_header(self.tracks_table, fit_columns=(0, 1, 2, 4))
        self.tracks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tracks_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        configure_data_view(self.tracks_table)
        install_copy_rows(self.tracks_table)
        header = self.tracks_table.horizontalHeader()
        header_h = header.sizeHint().height() if header is not None else 0
        self.tracks_table.setMinimumHeight(
            header_h + 2 * self.tracks_table.frameWidth() + _MIN_VISIBLE_ROWS * data_row_height(self.tracks_table)
        )
        self.tracks_table.hide()
        layout.addWidget(self.tracks_table)
        group.setLayout(layout)
        return group

    def _create_output_card(self) -> QFrame:
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
            self.tr(
                "Tracks are saved next to each video unless you choose a folder. "
                "A single ticked subtitle is named after its video, so mining finds it."
            )
        )
        # Deliberately NOT persisted. Off-by-default each launch is the safety
        # property: a remembered destructive default carries no reminder. Pinned
        # by tests/unit/test_run_option_persistence.py::test_overwrite_is_never_persisted.
        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing files"))
        self.overwrite_checkbox.setToolTip(
            self.tr("When unchecked, a track whose file already exists is skipped, not overwritten.")
        )
        layout.addWidget(self.overwrite_checkbox)
        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6)."""
        self.extract_button = ModernButton(self.tr("Extract Tracks"), variant="primary")
        self.extract_button.clicked.connect(self._on_extract)
        # Base slots (queue-finished re-enable) act on the tool's primary button.
        self._primary_button = self.extract_button
        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine state
    # ------------------------------------------------------------------

    def _probe_engine(self) -> Callable[[], object]:
        config = self.config
        return lambda: self._compute_ffmpeg_available(config)

    @staticmethod
    def _compute_ffmpeg_available(config: AnkiMinerConfig) -> bool:
        """Whether both ffmpeg (to copy) and ffprobe (to list) are reachable."""
        return binary_available(resolve_ffmpeg(config)) and binary_available(resolve_ffprobe(config))

    def _apply_probe_result(self, result: object) -> None:
        super()._apply_probe_result(result)
        # A config refresh mid-run re-probes; the run owns Extract until it ends.
        if still_running(self.worker_thread):
            self._primary_button.setEnabled(False)
        # A path set while ffmpeg was unknown is listed once it turns out present.
        if self._engine_is_available and self._probe is None and self.input_selector.path_or_none() is not None:
            self._probe_timer.start()

    def _typed_problem_summary(self, exc: BaseException) -> str | None:
        if isinstance(exc, FfmpegNotFoundError):
            return self.tr("ffmpeg could not be started. Install it and add it to PATH, then try again.")
        return None

    # ------------------------------------------------------------------
    # Track listing (a probe: never clears a screen issue)
    # ------------------------------------------------------------------

    def _on_input_changed(self, _text: str) -> None:
        self._probe = None
        self._probe_generation += 1
        self.tracks_table.setRowCount(0)
        self.tracks_table.hide()
        self.tracks_status_label.setText(self.tr("Choose a video to list its tracks."))
        if self._engine_is_available:
            self._probe_timer.start()

    def _probe_tracks(self) -> None:
        path_str = self.input_selector.path_or_none()
        if path_str is None or not self._engine_is_available:
            return
        source = Path(path_str)
        generation = self._probe_generation
        config = self.config
        codes = get_profile(config_language(config)).audio_track_codes
        self.tracks_status_label.setText(self.tr("Reading tracks…"))

        def _done(result: object) -> None:
            if generation == self._probe_generation:
                self._apply_input_probe(cast(InputProbe, result))

        def _failed(message: str) -> None:
            if generation != self._probe_generation:
                return
            self.tracks_status_label.setText(self.tr("Tracks could not be read."))
            self.show_screen_issue(ScreenIssue(summary=self.tr("Tracks could not be read."), details=message))

        run_off_thread(self, lambda: probe_input(source, resolve_ffprobe(config), codes), _done, _failed)

    def _apply_input_probe(self, probe: InputProbe) -> None:
        """Show *probe*'s tracks and tick its preselection."""
        self._probe_timer.stop()
        self._probe = probe
        table = self.tracks_table
        table.setRowCount(0)
        if not probe.videos:
            self.tracks_status_label.setText(self.tr("No videos were found in that folder."))
            table.hide()
            return
        first = probe.videos[0]
        if probe.tracks.is_empty:
            self.tracks_status_label.setText(
                tr_format(self.tr("No subtitle or audio tracks were found in %1."), first.name)
            )
            table.hide()
            return
        if probe.source != first:
            self.tracks_status_label.setText(
                tr_format(
                    self.tr(
                        "Tracks of %1, the first of %2 videos. The ticked tracks are saved from every video "
                        "in the folder; a video without one is skipped."
                    ),
                    first.name,
                    len(probe.videos),
                )
            )
        else:
            self.tracks_status_label.setText(tr_format(self.tr("Tracks of %1. Tick the ones to save."), first.name))
        ticked = set(probe.preselected)
        for sub in probe.tracks.subtitles:
            self._add_subtitle_row(sub, ticked)
        for audio in probe.tracks.audio:
            self._add_audio_row(audio, ticked)
        table.show()

    def _add_subtitle_row(self, stream: SubtitleStream, ticked: set[TrackRef]) -> None:
        ref = TrackRef("subtitle", stream.sub_index)
        flags = self._flags(default=stream.is_default, forced=stream.is_forced, image=not stream.is_text)
        flags_tip = (
            self.tr("Picture-based subtitles: saved as they are for an OCR tool. Mining can't read them.")
            if not stream.is_text
            else None
        )
        self._add_row(
            ref,
            tr_format(self.tr("Subtitle %1"), stream.sub_index + 1),
            stream.language_tag,
            _codec_label(stream.codec_name),
            track_format("subtitle", stream.codec_name).suffix,
            stream.title,
            flags,
            flags_tip,
            ref in ticked,
        )

    def _add_audio_row(self, stream: AudioStream, ticked: set[TrackRef]) -> None:
        ref = TrackRef("audio", stream.audio_index)
        codec = " ".join(part for part in (_codec_label(stream.codec), _format_channels(stream.channels)) if part)
        self._add_row(
            ref,
            tr_format(self.tr("Audio %1"), stream.audio_index + 1),
            stream.language_tag,
            codec,
            track_format("audio", stream.codec).suffix,
            stream.title_tag,
            self._flags(default=stream.is_default),
            None,
            ref in ticked,
        )

    def _add_row(
        self,
        ref: TrackRef,
        label: str,
        language: str | None,
        codec: str,
        suffix: str,
        title: str | None,
        flags: str,
        flags_tip: str | None,
        checked: bool,
    ) -> None:
        track = make_table_item(label)
        track.setFlags(track.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        track.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        track.setData(Qt.ItemDataRole.UserRole, ref)
        cells = (
            track,
            make_table_item(language or _NO_VALUE),
            make_table_item(codec, tooltip=tr_format(self.tr("Saved as %1"), suffix)),
            make_table_item(title or _NO_VALUE, tooltip=title),
            make_table_item(flags, tooltip=flags_tip),
        )
        row = self.tracks_table.rowCount()
        self.tracks_table.insertRow(row)
        for col, cell in enumerate(cells):
            self.tracks_table.setItem(row, col, cell)

    def _flags(self, *, default: bool, forced: bool = False, image: bool = False) -> str:
        parts = []
        if default:
            parts.append(self.tr("Default"))
        if forced:
            parts.append(self.tr("Forced"))
        if image:
            parts.append(self.tr("Image"))
        return " · ".join(parts)

    def ticked_refs(self) -> tuple[TrackRef, ...]:
        """The ticked tracks, in table order."""
        refs: list[TrackRef] = []
        for row in range(self.tracks_table.rowCount()):
            item = self.tracks_table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                refs.append(cast(TrackRef, item.data(Qt.ItemDataRole.UserRole)))
        return tuple(refs)

    # ------------------------------------------------------------------
    # Extract (the run entry)
    # ------------------------------------------------------------------

    def _on_extract(self) -> None:
        """Validate the input and the ticks, then start the TrackExtractWorker."""
        if not self._engine_is_available:
            return
        # Reentrancy guard: never reassign self.worker_thread over a live thread.
        if still_running(self.worker_thread):
            return
        # A fresh attempt supersedes the complaint about the last one (D24).
        self.clear_screen_issue()

        path_str = self.input_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a video or a folder first.")))
            return
        source = Path(path_str)
        if not source.exists():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That file or folder no longer exists."), details=path_str)
            )
            return
        probe = self._probe
        if probe is None or probe.source != source:
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Wait for the track list to load, then tick the tracks to save."))
            )
            return
        if not probe.videos:
            self.show_screen_issue(ScreenIssue(summary=self.tr("No videos were found in that folder.")))
            return
        ticked = self.ticked_refs()
        if not ticked:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Tick at least one track to save.")))
            return
        # Captured now: a path change during the folder scan drops the listing.
        listed = {ref: getattr(probe.tracks.find(ref), "language_tag", None) for ref in ticked}

        if source.is_dir():
            # Listed again, off the GUI thread: the folder may have changed since the probe.
            self.extract_button.setEnabled(False)

            def _on_files(files: list[Path]) -> None:
                if not files:
                    self.extract_button.setEnabled(True)
                    return
                self._start_run(files, ticked, listed)

            self._scan_folder_async(
                source,
                is_track_input,
                _on_files,
                sort_key=lambda f: natural_sort_key(f.name),
                empty_summary=self.tr("No videos were found in that folder."),
                failed_summary=self.tr("That folder could not be scanned."),
            )
            return
        self._start_run([source], ticked, listed)

    def _start_run(self, videos: list[Path], ticked: tuple[TrackRef, ...], listed: dict[TrackRef, str | None]) -> None:
        check_dir = self._custom_output_dir or videos[0].parent
        if not self._output_dir_writable(check_dir, self.tr("Output folder is not writable.")):
            self.extract_button.setEnabled(True)
            return
        self._total_files = len(videos)
        self._begin_tool_run(len(videos))
        self.log_widget.clear_log()
        self.progress_widget.reset()
        worker = TrackExtractWorker(
            self.config,
            videos,
            ticked,
            output_dir=self._custom_output_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
            expected_languages=listed,
        )
        worker.file_note.connect(self._on_file_note)
        self._start_queue_worker(worker)

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Saving tracks from video %1 of %2"), idx + 1, self._total_files)
        )

    def _on_file_note(self, _idx: int, note: str) -> None:
        self.log_widget.append_info(note)

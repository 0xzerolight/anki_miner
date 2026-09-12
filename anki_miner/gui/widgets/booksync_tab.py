"""Audiobook Sync tab (Utilities → Audiobook Sync).

Pick an audiobook — one file, or a folder of per-chapter files — and the
book it reads (``.epub`` or ``.txt``); the tool transcribes the audio and
writes ``<audio stem>.srt`` whose cues are the book's own sentences, timed
from the transcript (:mod:`anki_miner.services.book_sync`). The output feeds
the Audiobook mining tab, Reading → Subtitles, or any reader.

Structure and idioms are cloned from
:mod:`anki_miner.gui.widgets.subtitle_creation_tab` (Generate): mode toggle,
off-thread folder scan, engine probe, model guard, output section, worker
lifecycle on :class:`~anki_miner.gui.widgets._tool_tab_base._ToolTabBase`.

Guard contract:
- ASR engine not importable → Sync disabled, notice visible.
- No book / wrong extension / missing file → screen issue, nothing starts.
- Model not ready → banner with the Transcription Settings action.
- Output folder not writable → screen issue.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import reveal_settings
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout, field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.widgets.subtitle_creation_tab import _AUDIO_EXTENSIONS
from anki_miner.gui.workers.booksync_worker import BookSyncWorker
from anki_miner.services.asr import _engine
from anki_miner.services.asr.model_availability import usable_model_installed
from anki_miner.services.reading._util import is_junk_path, natural_sort_key
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# _AUDIO_EXTENSIONS is imported from subtitle_creation_tab (import block above):
# the same audio set Generate accepts, not a third copy (audiobook_tab carries
# the other duplicate; folding all three is out of this change's scope).
_AUDIO_FILE_FILTER = "Audio Files (" + " ".join(f"*{e}" for e in sorted(_AUDIO_EXTENSIONS)) + ");;All Files (*)"
_BOOK_EXTENSIONS: frozenset[str] = frozenset({".epub", ".txt"})
_BOOK_FILE_FILTER = "Books (*.epub *.txt);;All Files (*)"


class BookSyncTab(_ToolTabBase):
    """Sync an audiobook to its book; one .srt per audio file.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "tools.booksync"
    TASK_OWNER = CapabilityTarget("subtitles", "booksync")

    #: Where this tool last wrote — remembered separately from its inputs (D7).
    OUTPUT_HISTORY_KEY = "tools.booksync.output"

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
            partial=self.tr("Finished with errors — see log"),
            run_problem=self.tr("Some audio files could not be synced."),
            complete_template=self.tr("Complete — %1 file(s) synced"),
            complete_skipped_template=self.tr("Complete — %1 synced, %2 skipped"),
            all_skipped_template=self.tr("No subtitles written — all %1 skipped; see log."),
            select_output_folder=self.tr("Select Output Folder"),
            output_default=self.tr("Next to the audio"),
            task_title=self.tr("Audiobook sync"),
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

        Reached through ``SubtitlesTab.update_config``; a run already in flight
        keeps the config it captured at construction.
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

        layout.addWidget(self._create_audio_section())
        layout.addWidget(self._create_book_section())
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

    def _create_audio_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Audiobook")))

        # Engine notice (shown when engine unavailable) — same sentence as Generate.
        self.engine_notice_label = QLabel(
            self.tr("Transcription is not ready. Open Settings → Transcription & Alignment to finish setup.")
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        desc = QLabel(
            self.tr(
                "Transcribes the audiobook and times the book's own sentences to it, writing an .srt "
                "beside each audio file. A folder is read as one book in file-name order."
            )
        )
        desc.setObjectName("helper-text")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACING.xs)
        mode_row.addWidget(QLabel(self.tr("Mode:")))

        self.file_mode_button = ModernButton(self.tr("Single File"), variant="secondary")
        self.file_mode_button.setCheckable(True)
        self.file_mode_button.setChecked(True)
        self.file_mode_button.setToolTip(self.tr("Sync one audio file to the book."))
        self.file_mode_button.clicked.connect(self._on_file_mode)
        mode_row.addWidget(self.file_mode_button)

        self.folder_mode_button = ModernButton(self.tr("Folder"), variant="secondary")
        self.folder_mode_button.setCheckable(True)
        self.folder_mode_button.setChecked(False)
        self.folder_mode_button.setToolTip(
            self.tr("Every audio file in the folder, in file-name order, one .srt each.")
        )
        self.folder_mode_button.clicked.connect(self._on_folder_mode)
        mode_row.addWidget(self.folder_mode_button)

        mode_row.addStretch()
        layout.addLayout(mode_row)

        # The book selector's label is the longest of the three; align all on it.
        width = field_label_width(self.tr("EPUB or Text File:"))

        # File selector (single-file mode)
        self.file_selector = FileSelector(
            label=self.tr("Audio File:"),
            file_mode=True,
            file_filter=_AUDIO_FILE_FILTER,
            label_width=width,
            history_key="tools.booksync.inputs",
            drop_validator=accepts_suffixes(_AUDIO_EXTENSIONS, self.tr("This field takes an audio file.")),
        )
        layout.addWidget(self.file_selector)

        # Folder selector (folder mode, hidden by default)
        self.folder_selector = FileSelector(
            label=self.tr("Audio Folder:"),
            file_mode=False,
            label_width=width,
            history_key="tools.booksync.inputs",
        )
        self.folder_selector.hide()
        layout.addWidget(self.folder_selector)

        group.setLayout(layout)
        return group

    def _create_book_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Book")))

        self.book_selector = FileSelector(
            label=self.tr("EPUB or Text File:"),
            file_mode=True,
            file_filter=_BOOK_FILE_FILTER,
            label_width=field_label_width(self.tr("EPUB or Text File:")),
            history_key="tools.booksync.inputs",
            drop_validator=accepts_suffixes(_BOOK_EXTENSIONS, self.tr("This field takes an .epub or .txt file.")),
        )
        layout.addWidget(self.book_selector)

        group.setLayout(layout)
        return group

    def _create_output_section(self) -> QFrame:
        # Verbatim shape of SubtitleCreationTab._create_output_section, with this tab's strings.
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Output")))

        out_desc = QLabel(self.tr("Each .srt is saved next to its audio file unless you choose a folder."))
        out_desc.setObjectName("helper-text")
        out_desc.setWordWrap(True)
        layout.addWidget(out_desc)

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

        # Deliberately NOT persisted. Off-by-default each launch is the safety
        # property: a remembered destructive default carries no reminder. Pinned
        # by tests/unit/test_run_option_persistence.py::test_overwrite_is_never_persisted.
        self.overwrite_checkbox = QCheckBox(self.tr("Overwrite existing SRT files"))
        self.overwrite_checkbox.setToolTip(
            self.tr("When unchecked, audio files that already have an .srt file are skipped, not overwritten.")
        )
        layout.addWidget(self.overwrite_checkbox)

        group.setLayout(layout)
        return group

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6)."""
        self.sync_button = ModernButton(self.tr("Sync Audiobook"), variant="primary")
        self.sync_button.clicked.connect(self._on_sync)
        # Base slots (queue-finished re-enable) act on the tool's primary button.
        self._primary_button = self.sync_button

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine / model state
    # ------------------------------------------------------------------

    def _refresh_engine_state(self) -> None:
        """Probe engine availability off-thread, then update the Sync guard."""
        self.sync_button.setEnabled(False)
        if self._suppress_optional_startup:
            return

        def _apply(result: object) -> None:
            self._engine_is_available = bool(result)
            self.engine_notice_label.setVisible(not self._engine_is_available)
            self.sync_button.setEnabled(self._engine_is_available)

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
    # Sync
    # ------------------------------------------------------------------

    def _on_sync(self) -> None:
        """Validate then start the BookSyncWorker."""
        if not self._engine_is_available:
            # Should not happen (button disabled), but guard anyway.
            return

        # Reentrancy guard: a prior run's QThread may still be tearing down when
        # queue_finished re-enabled the button. Never reassign self.worker_thread
        # over a live thread.
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        # A fresh attempt supersedes the complaint about the last one (D24).
        self.clear_screen_issue()

        book = self._collect_book()
        if book is None:
            return

        if not self.file_selector.isHidden():
            # Single-file mode: no directory scan, stays synchronous.
            audio_files = self._collect_single_audio_file()
            if not audio_files:
                return
            self._continue_sync(audio_files, book)
            return

        # Folder mode: the directory listing can stall on a network share —
        # run it off the GUI thread and continue from its completion
        # callback. Disabled here (not just at worker-start) so a second
        # click during the scan can't fire a second concurrent scan.
        self.sync_button.setEnabled(False)

        def _on_files(audio_files: list[Path]) -> None:
            if not audio_files:
                self.sync_button.setEnabled(True)
                return
            self._continue_sync(audio_files, book)

        self._collect_folder_audio_files_async(_on_files)

    def _collect_book(self) -> Path | None:
        """Return the chosen book path, or ``None`` after showing why it was refused."""
        path_str = self.book_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose the book (.epub or .txt) before syncing.")))
            return None
        book = Path(path_str)
        if book.suffix.lower() not in _BOOK_EXTENSIONS:
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Pick an .epub or .txt file for the book."), details=path_str)
            )
            return None
        if not book.is_file():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That book file no longer exists."), details=path_str))
            return None
        return book

    def _collect_single_audio_file(self) -> list[Path]:
        """Single-file mode: return [audio], or [] on validation failure."""
        path_str = self.file_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose an audio file before syncing.")))
            return []
        audio = Path(path_str)
        if not audio.is_file():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That audio file no longer exists."), details=path_str))
            return []
        return [audio]

    def _collect_folder_audio_files_async(self, on_files: Callable[[list[Path]], None]) -> None:
        """Folder mode: scan the folder off the GUI thread, then call ``on_files``
        on the GUI thread with the audio files in natural file-name order
        (``[]`` on failure — screen-issue feedback already shown).
        """
        path_str = self.folder_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a folder before syncing.")))
            on_files([])
            return
        folder = Path(path_str)
        if not folder.is_dir():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder no longer exists."), details=path_str))
            on_files([])
            return

        def _scan() -> object:
            # Natural order (1, 2, 10), junk and AppleDouble sidecars dropped:
            # the files are one book read in sequence, so order is meaning.
            return sorted(
                (
                    f
                    for f in folder.iterdir()
                    if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS and not is_junk_path(f.name)
                ),
                key=lambda f: natural_sort_key(f.name),
            )

        def _apply(result: object) -> None:
            files = cast("list[Path]", result)
            if not files:
                self.show_screen_issue(ScreenIssue(summary=self.tr("No audio files were found in that folder.")))
                on_files([])
                return
            on_files(files)

        def _on_error(msg: str) -> None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder could not be scanned."), details=msg))
            on_files([])

        run_off_thread(self, _scan, _apply, _on_error)

    def _continue_sync(self, audio_files: list[Path], book: Path) -> None:
        """Check writability + the model, then start the worker."""
        out_dir = self._custom_output_dir

        # Pre-run writable check. When out_dir is None every output lands
        # next to its audio, so check the first audio file's parent.
        check_dir = out_dir if out_dir is not None else audio_files[0].parent
        if not os.access(check_dir, os.W_OK):
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Output folder is not writable."), details=str(check_dir))
            )
            self.sync_button.setEnabled(True)
            return

        # Model-downloaded guard — the same rule as Generate's, for the same
        # reasons (see subtitle_creation_tab._continue_generate): cheap on-disk
        # checks on the GUI thread, no Vulkan probe.
        if not usable_model_installed(self.config):
            self.show_screen_issue(
                ScreenIssue(
                    summary=tr_format(self.tr("The transcription model %1 is not ready."), self.config.asr_model),
                    action_id="settings.subtitles",
                    action_text=self.tr("Open Transcription Settings"),
                ),
                action=lambda: reveal_settings(self, "subtitles"),
            )
            self.sync_button.setEnabled(True)
            return

        # Build and start worker
        self._begin_tool_run(len(audio_files))
        self._total_files = len(audio_files)
        self.log_widget.clear_log()
        self.progress_widget.reset()
        self.log_widget.append_info(tr_format(self.tr("Book: %1"), book.name))

        worker = BookSyncWorker(
            self.config,
            audio_files,
            book,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
        )
        self.worker_thread = worker

        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.file_skipped.connect(self._on_file_skipped)
        worker.queue_finished.connect(self._on_queue_finished)
        worker.error.connect(self._on_run_error)
        # Lifecycle: free the QThread on real thread exit (not on queue_finished,
        # which fires just before the thread ends).
        worker.finished.connect(self._on_worker_finished)

        self.sync_button.setEnabled(False)
        self.cancel_button.show()

        worker.start()

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Syncing file %1 of %2"), str(idx + 1), str(self._total_files))
        )

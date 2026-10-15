"""Audiobook Sync tab (Utilities → Audiobook Sync).

Pick an audiobook — one file, or a folder of per-chapter files — and the
book it reads (``.epub`` or ``.txt``); the tool transcribes the audio and
writes ``<audio stem>.srt`` whose cues are the book's own sentences, timed
from the transcript (:mod:`anki_miner.services.book_sync`). The output feeds
the Audiobook mining tab, Reading → Subtitles, or any reader.

Folder mode also takes a shelf (WP-27): a folder with no audio of its own
whose subfolders each hold one book and its audio
(:func:`~anki_miner.services.book_sync.library.scan_book_folder`). The books
run in order in one worker, and the Book card's picker is not needed. After a
run each synced book gets one report line (sentence %, audio %, grade; MS-25)
under the Book card and in the Activity log.

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

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtWidgets import QCheckBox, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.constants import AUDIO_EXTENSIONS
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import reveal_settings
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout, field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, accepts_suffixes
from anki_miner.gui.workers.booksync_worker import BookSyncWorker
from anki_miner.models import TerminalOutcome
from anki_miner.services.asr import _engine
from anki_miner.services.asr.model_availability import usable_model_installed
from anki_miner.services.book_sync.library import BookJob, FolderScan, SkipReason, scan_book_folder
from anki_miner.services.book_sync.report import BookSyncReport
from anki_miner.utils.i18n import tr_format

_AUDIO_FILE_FILTER = "Audio Files (" + " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS)) + ");;All Files (*)"
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

    _PROBE_NAME = "ASR"

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
        #: This run's report lines, one per synced book (MS-25).
        self._report_lines: list[str] = []
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
            run_problem_single=self.tr("This audio file could not be synced."),
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

        self._build_mode_row(
            layout,
            mode_label=self.tr("Mode:"),
            single_label=self.tr("Single File"),
            folder_label=self.tr("Folder"),
            single_tip=self.tr("Sync one audio file to the book."),
            folder_tip=self.tr(
                "Every audio file in the folder, in file-name order, one .srt each. A folder of book "
                "folders (each with its EPUB and audio) syncs every book in turn."
            ),
        )

        # The book selector's label is the longest of the three; align all on it.
        width = field_label_width(self.tr("EPUB or Text File:"))

        # File selector (single-file mode)
        self.file_selector = FileSelector(
            label=self.tr("Audio File:"),
            file_mode=True,
            file_filter=_AUDIO_FILE_FILTER,
            label_width=width,
            history_key="tools.booksync.inputs",
            drop_validator=accepts_suffixes(AUDIO_EXTENSIONS, self.tr("This field takes an audio file.")),
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

        # MS-25: how well the last run synced, one line per book. Inline, not a
        # dialog (D24); hidden until a run has something to report.
        self.report_label = QLabel()
        self.report_label.setObjectName("helper-text")
        self.report_label.setWordWrap(True)
        self.report_label.setToolTip(
            self.tr(
                "Sentences: how many of the book's sentences, from the first one found to the last, got a "
                "subtitle. Audio: how much of the narration matched the book. Grade: A from 90% on both, "
                "B from 75%, C from 50%, else D."
            )
        )
        self.report_label.hide()
        layout.addWidget(self.report_label)

        group.setLayout(layout)
        return group

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
        # E08: the helper line became the Choose Folder… tooltip.
        self.choose_output_button.setToolTip(
            self.tr("Each .srt is saved next to its audio file unless you choose a folder.")
        )

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

    def _probe_engine(self) -> Callable[[], object]:
        """Probe ASR engine availability (the Sync guard)."""
        return _engine.available

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------

    def _apply_mode(self, single: bool) -> None:
        self.file_selector.setVisible(single)
        self.folder_selector.setVisible(not single)

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

        if not self.file_selector.isHidden():
            # Single-file mode: no directory scan, stays synchronous.
            book = self._collect_book()
            if book is None:
                return
            audio_files = self._collect_single_audio_file()
            if not audio_files:
                return
            self._continue_sync(audio_files, book)
            return

        # Folder mode: the directory listing can stall on a network share —
        # run it off the GUI thread and continue from its completion
        # callback. Disabled here (not just at worker-start) so a second
        # click during the scan can't fire a second concurrent scan. The
        # book is asked for only once the scan says the folder is one book
        # (a shelf names its books itself, WP-27).
        self.sync_button.setEnabled(False)

        def _on_scan(scan: FolderScan | None) -> None:
            if scan is None:
                self.sync_button.setEnabled(True)
                return
            if scan.books:
                self._continue_shelf(scan)
                return
            book = self._collect_book()
            if book is None:
                self.sync_button.setEnabled(True)
                return
            self._continue_sync(list(scan.audio_files), book)

        self._scan_folder_for_books_async(_on_scan)

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

    def _scan_folder_for_books_async(self, on_scan: Callable[[FolderScan | None], None]) -> None:
        """Folder mode: classify the folder off the GUI thread, then call ``on_scan``
        on the GUI thread with what it holds (``None`` on refusal — the screen
        issue already says why).
        """
        path_str = self.folder_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a folder before syncing.")))
            on_scan(None)
            return
        folder = Path(path_str)
        if not folder.is_dir():
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder no longer exists."), details=path_str))
            on_scan(None)
            return

        def _apply(result: object) -> None:
            self._folder_scan_pending = False
            scan = result if isinstance(result, FolderScan) else FolderScan()
            if not scan.audio_files and not scan.books:
                self.show_screen_issue(
                    ScreenIssue(
                        summary=self.tr(
                            "No audio files were found in that folder, and no book folders (each with its "
                            "book and audio)."
                        ),
                        details="\n".join(self._skip_line(name, reason) for name, reason in scan.skipped),
                    )
                )
                on_scan(None)
                return
            on_scan(scan)

        def _on_error(message: str) -> None:
            self._folder_scan_pending = False
            self.show_screen_issue(ScreenIssue(summary=self.tr("That folder could not be scanned."), details=message))
            on_scan(None)

        # Natural order (1, 2, 10): the files are one book read in sequence,
        # and the books of a shelf are volumes, so order is meaning.
        self._folder_scan_pending = True
        run_off_thread(self, lambda: scan_book_folder(folder, AUDIO_EXTENSIONS), _apply, _on_error)

    def _skip_line(self, name: str, reason: SkipReason) -> str:
        """One translated line saying why a subfolder of a shelf is not a book."""
        if reason is SkipReason.NO_AUDIO:
            return tr_format(self.tr("Skipped folder %1: no audio files"), name)
        if reason is SkipReason.NO_BOOK:
            return tr_format(self.tr("Skipped folder %1: no .epub or .txt book"), name)
        return tr_format(self.tr("Skipped folder %1: more than one book"), name)

    def _ready_to_run(self, check_dir: Path) -> bool:
        """The pre-run guards every run passes: a writable output folder and a ready model."""
        if not self._output_dir_writable(check_dir, self.tr("Output folder is not writable.")):
            self.sync_button.setEnabled(True)
            return False

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
            return False
        return True

    def _continue_sync(self, audio_files: list[Path], book: Path) -> None:
        """One book: check writability + the model, then start the worker."""
        out_dir = self._custom_output_dir
        # When out_dir is None every output lands next to its audio, so check
        # the first audio file's parent.
        if not self._ready_to_run(out_dir if out_dir is not None else audio_files[0].parent):
            return
        self._open_run(len(audio_files))
        self.log_widget.append_info(tr_format(self.tr("Book: %1"), book.name))
        worker = BookSyncWorker(
            self.config,
            audio_files,
            book,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
        )
        self._start_sync_worker(worker)

    def _continue_shelf(self, scan: FolderScan) -> None:
        """A shelf (WP-27): the same guards, then one worker for every book in order."""
        books: tuple[BookJob, ...] = scan.books
        out_dir = self._custom_output_dir
        if not self._ready_to_run(out_dir if out_dir is not None else books[0].audio_files[0].parent):
            return
        self._open_run(sum(len(job.audio_files) for job in books))
        self.log_widget.append_info(
            tr_format(self.tr("Books: %1 — %2"), len(books), ", ".join(job.book.name for job in books))
        )
        for name, reason in scan.skipped:
            self.log_widget.append_info(self._skip_line(name, reason))
        worker = BookSyncWorker(
            self.config,
            [],
            None,
            books=books,
            output_dir=out_dir,
            overwrite=self.overwrite_checkbox.isChecked(),
        )
        self._start_sync_worker(worker)

    def _open_run(self, total_files: int) -> None:
        """Publish the run and clear what the last one left on the page."""
        self._begin_tool_run(total_files)
        self._total_files = total_files
        self._report_lines = []
        self.report_label.clear()
        self.report_label.hide()
        self.log_widget.clear_log()
        self.progress_widget.reset()

    def _start_sync_worker(self, worker: BookSyncWorker) -> None:
        """Connect the per-book report, then hand the worker to the shared launch."""
        worker.book_report.connect(self._on_book_report)
        self._start_queue_worker(worker)

    def _on_book_report(self, report: object) -> None:
        """One synced book's report line, logged now and shown when the run ends (MS-25)."""
        if not isinstance(report, BookSyncReport):
            return
        line = tr_format(
            self.tr("%1: sentences %2%, audio %3% — grade %4"),
            report.book_name,
            report.sentence_pct,
            report.audio_pct,
            report.grade,
        )
        self._report_lines.append(line)
        self.log_widget.append_info(line)

    def _on_queue_finished(self, outcome: object = TerminalOutcome.SUCCESS) -> None:
        super()._on_queue_finished(outcome)
        if self._report_lines:
            self.report_label.setText("\n".join(self._report_lines))
            self.report_label.show()

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(
            tr_format(self.tr("Syncing file %1 of %2"), str(idx + 1), str(self._total_files))
        )

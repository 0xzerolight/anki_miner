"""Readability tab (Utilities → Readability, #132).

Pick a subtitle file or a folder of them; the tool shows how much of each file,
and of the whole set, the learner already knows: the share of word occurrences
known, how many different new words there are, and the share of lines with no
new word (i+0), one (i+1) or more. Known words are read the way mining reads
them (:mod:`anki_miner.services.readability`); nothing is written.

Structure follows :mod:`anki_miner.gui.widgets.booksync_tab` on
:class:`~anki_miner.gui.widgets._tool_tab_base._ToolTabBase`, without an Output
row or a mode toggle: one field takes a file or a folder (D7-B).

Guard contract:
- The mining language cannot tokenize here → Check disabled, notice visible.
- No path / missing path / not a subtitle / empty folder → screen issue, nothing starts.
- Anki unreachable → the run fails with its own banner sentence, never a 0% report.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QTableWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.constants import SUBTITLE_FILE_FILTER
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import (
    CellRole,
    SortableTableWidgetItem,
    configure_data_view,
    configure_table_header,
    data_row_height,
    hold_numeric_columns,
    install_copy_rows,
    make_table_item,
)
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase, _ToolTabStrings
from anki_miner.gui.widgets.base import PageWidth, ScreenIssue, configure_card_layout, field_label_width
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader, StatCard
from anki_miner.gui.workers.readability_worker import ReadabilityWorker
from anki_miner.languages.registry import config_language, get_profile, subtitle_language
from anki_miner.models.readability import I_PLUS_0, I_PLUS_1, I_PLUS_2_OR_MORE, ReadabilityStats
from anki_miner.services.readability import combine
from anki_miner.services.reading._util import natural_sort_key
from anki_miner.utils.file_pairing import FilePairMatcher, SubtitleTag, subtitle_language_tag
from anki_miner.utils.i18n import tr_format

_SUBTITLE_EXTENSIONS = FilePairMatcher.SUBTITLE_EXTENSIONS
#: Known, New words, i+0, i+1, i+2+ (File is text).
_NUMBER_COLUMNS = (1, 2, 3, 4, 5)
#: Rows the table shows before it scrolls (a floor in rows, not pixels).
_MIN_VISIBLE_ROWS = 8
_NO_VALUE = "—"


def _pct(value: float | None, decimals: int) -> str:
    return _NO_VALUE if value is None else f"{value:.{decimals}f}%"


def _pct_cell(value: float | None, decimals: int) -> SortableTableWidgetItem:
    return make_table_item(_pct(value, decimals), CellRole.NUMBER, sort_value=-1.0 if value is None else value)


class ReadabilityTab(_ToolTabBase):
    """How much of a subtitle file, or a folder of them, the learner already knows.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    #: A label beside its control; a wider window buys gutters, not longer inputs.
    PAGE_WIDTH = PageWidth.PAGE

    #: Published so this screen's Cancel gets a live wait clock and the
    #: pinned bar gets a stage and a progress bar (D17, D22).
    TASK_ID = "tools.readability"
    TASK_OWNER = CapabilityTarget("subtitles", "readability")

    #: The tool only reads; there is no output folder to remember.
    OUTPUT_HISTORY_KEY = ""

    _PROBE_NAME = "Language engine"

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
        self._run_files: list[Path] = []
        self._measured: list[ReadabilityStats] = []
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
            run_problem=self.tr("Some subtitle files could not be read."),
            run_problem_single=self.tr("This subtitle file could not be read."),
            complete_template=self.tr("Complete — %1 file(s) checked"),
            complete_skipped_template=self.tr("Complete — %1 checked, %2 skipped (no words in the mining language)"),
            all_skipped_template=self.tr("Nothing to report — no file had words in the mining language."),
            select_output_folder="",
            output_default="",
            task_title=self.tr("Readability check"),
        )

        self._setup_ui()
        self._refresh_engine_state()

    def _item_total(self) -> int:
        return self._total_files

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new application config (e.g. after a mining-language switch).

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

        layout.addWidget(self._create_input_card())
        layout.addWidget(self._create_report_card())
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

        layout.addWidget(SectionHeader(self.tr("Subtitles")))

        self.engine_notice_label = QLabel(
            self.tr(
                "Your mining language can't read text on this computer yet. "
                "Download its language pack in Settings → Mining Language."
            )
        )
        self.engine_notice_label.setObjectName("helper-text")
        self.engine_notice_label.setWordWrap(True)
        self.engine_notice_label.hide()
        layout.addWidget(self.engine_notice_label)

        desc = QLabel(
            self.tr(
                "See how much of a subtitle file, or a folder of them, you already know. "
                "Uses your Anki cards and known words; nothing is written."
            )
        )
        desc.setObjectName("helper-text")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        wrong_kind = self.tr("This field takes a subtitle file or a folder.")
        self.input_selector = FileSelector(
            label=self.tr("Subtitle file or folder:"),
            file_mode=True,
            allow_folder=True,
            file_filter=SUBTITLE_FILE_FILTER,
            label_width=field_label_width(self.tr("Subtitle file or folder:")),
            history_key="tools.readability.inputs",
            drop_validator=lambda p: (p.is_dir() or p.suffix.lower() in _SUBTITLE_EXTENSIONS, wrong_kind),
        )
        layout.addWidget(self.input_selector)

        group.setLayout(layout)
        return group

    def _create_report_card(self) -> QFrame:
        self.report_card = QFrame()
        self.report_card.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Report")))

        cards = QHBoxLayout()
        cards.setSpacing(SPACING.sm)
        self.known_card = self._stat_card(
            self.tr("Known words"),
            self.tr("Share of all words in these files you already know. A word said ten times counts ten times."),
        )
        self.new_words_card = self._stat_card(
            self.tr("New words"),
            self.tr("Different words you don't know yet, counted once across all files."),
        )
        self.i0_card = self._stat_card(self.tr("Lines fully known"), self.tr("Lines with no new words (i+0)."))
        self.i1_card = self._stat_card(
            self.tr("Lines with one new word"),
            self.tr("Lines with exactly one new word (i+1): the easiest sentences to learn from."),
        )
        for card in (self.known_card, self.new_words_card, self.i0_card, self.i1_card):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.files_table = QTableWidget(0, 6)
        headers = (
            (self.tr("File"), self.tr("Subtitle file.")),
            (self.tr("Known"), self.tr("Share of the file's words you already know.")),
            (
                self.tr("New words"),
                self.tr("Different words in this file you don't know yet. The total above counts a word once."),
            ),
            ("i+0", self.tr("Lines with no new words.")),
            ("i+1", self.tr("Lines with exactly one new word.")),
            ("i+2+", self.tr("Lines with two or more new words.")),
        )
        self.files_table.setHorizontalHeaderLabels([label for label, _tip in headers])
        for col, (_label, tip) in enumerate(headers):
            item = self.files_table.horizontalHeaderItem(col)
            if item is not None:
                item.setToolTip(tip)
        configure_table_header(self.files_table, fit_columns=_NUMBER_COLUMNS)
        self.files_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Qt's default indicator is column 0 descending, which would list the
        # files backwards; File sorts on the run index, so this is run order.
        header = self.files_table.horizontalHeader()
        if header is not None:
            header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.files_table.setSortingEnabled(True)
        configure_data_view(self.files_table)
        install_copy_rows(self.files_table)
        header_h = header.sizeHint().height() if header is not None else 0
        self.files_table.setMinimumHeight(
            header_h + 2 * self.files_table.frameWidth() + _MIN_VISIBLE_ROWS * data_row_height(self.files_table)
        )
        layout.addWidget(self.files_table)

        self.report_card.setLayout(layout)
        self.report_card.hide()
        return self.report_card

    @staticmethod
    def _stat_card(label: str, tooltip: str) -> StatCard:
        card = StatCard(_NO_VALUE, label)
        card.setToolTip(tooltip)
        return card

    def _create_action_buttons(self) -> None:
        """Build the two run controls. They live in the pinned bar (D6)."""
        self.check_button = ModernButton(self.tr("Check Readability"), variant="primary")
        self.check_button.clicked.connect(self._on_check)
        # Base slots (queue-finished re-enable) act on the tool's primary button.
        self._primary_button = self.check_button

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.hide()

    # ------------------------------------------------------------------
    # Engine state
    # ------------------------------------------------------------------

    def _probe_engine(self) -> Callable[[], object]:
        """Probe whether the mining language can tokenize (the Check guard)."""
        config = self.config
        return lambda: self._compute_engine_available(config)

    @staticmethod
    def _compute_engine_available(config: AnkiMinerConfig) -> bool:
        """Whether the mining language's engine is installed (find_spec only; ja has no probe)."""
        probe = get_profile(config_language(config)).unavailable_reason
        return probe is None or not probe()

    def _apply_probe_result(self, result: object) -> None:
        super()._apply_probe_result(result)
        # A config refresh mid-run re-probes; the run owns Check until it ends.
        if still_running(self.worker_thread):
            self._primary_button.setEnabled(False)

    def _typed_problem_summary(self, exc: BaseException) -> str | None:
        if isinstance(exc, AnkiConnectionError):
            return self.tr("Anki isn't reachable. Start Anki (with AnkiConnect) and check again.")
        return None

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def _on_check(self) -> None:
        """Validate the input, then start the ReadabilityWorker."""
        if not self._engine_is_available:
            # Should not happen (button disabled), but guard anyway.
            return
        # Reentrancy guard: never reassign self.worker_thread over a live thread.
        if still_running(self.worker_thread):
            return

        # A fresh attempt supersedes the complaint about the last one (D24).
        self.clear_screen_issue()

        path_str = self.input_selector.path_or_none()
        if path_str is None:
            self.show_screen_issue(ScreenIssue(summary=self.tr("Choose a subtitle file or a folder first.")))
            return
        path = Path(path_str)

        if path.is_dir():
            # The listing can stall on a network share: off the GUI thread,
            # with Check held off so a second click cannot start a second scan.
            self.check_button.setEnabled(False)

            def _on_files(files: list[Path]) -> None:
                if not files:
                    self.check_button.setEnabled(True)
                    return
                self._start_run(files)

            # A folder often holds the same episodes in other languages too
            # (ep01.en.srt beside ep01.es.srt); scoring those would skew the
            # totals, and a Latin-script mining language cannot tell them apart.
            language = subtitle_language(config_language(self.config))
            # Natural order (1, 2, 10): the rows read as the season does.
            self._scan_folder_async(
                path,
                lambda f: f.suffix.lower() in _SUBTITLE_EXTENSIONS
                and subtitle_language_tag(f, language) is not SubtitleTag.OTHER,
                _on_files,
                sort_key=lambda f: natural_sort_key(f.name),
                empty_summary=self.tr("No subtitle files were found in that folder."),
                failed_summary=self.tr("That folder could not be scanned."),
            )
            return

        if not path.is_file():
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("That file or folder no longer exists."), details=path_str)
            )
            return
        if path.suffix.lower() not in _SUBTITLE_EXTENSIONS:
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Pick a subtitle file (.ass, .srt, .ssa, .vtt or .smi)."), details=path_str)
            )
            return
        self._start_run([path])

    def _start_run(self, files: list[Path]) -> None:
        self._total_files = len(files)
        self._run_files = list(files)
        # Every run starts empty; a cancelled run keeps what it measured.
        self._clear_report()
        self._begin_tool_run(len(files))
        self.log_widget.clear_log()
        self.progress_widget.reset()

        worker = ReadabilityWorker(self.config, files)
        worker.file_measured.connect(self._on_file_measured)
        self._start_queue_worker(worker)

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _on_file_started(self, idx: int) -> None:
        self.progress_widget.set_status(tr_format(self.tr("Checking file %1 of %2"), idx + 1, self._total_files))

    def _on_file_measured(self, idx: int, stats: object) -> None:
        assert isinstance(stats, ReadabilityStats)
        self._measured.append(stats)
        self._append_row(idx, self._run_files[idx], stats)
        self._refresh_totals()
        self.report_card.show()

    def _append_row(self, idx: int, path: Path, stats: ReadabilityStats) -> None:
        table = self.files_table
        cells = (
            # File sorts on the run index, so ascending is the order the run read them.
            make_table_item(path.name, sort_value=idx, tooltip=str(path)),
            _pct_cell(stats.known_pct, 1),
            make_table_item(f"{len(stats.new_words):,}", CellRole.NUMBER, sort_value=len(stats.new_words)),
            _pct_cell(stats.line_pct(I_PLUS_0), 0),
            _pct_cell(stats.line_pct(I_PLUS_1), 0),
            _pct_cell(stats.line_pct(I_PLUS_2_OR_MORE), 0),
        )
        # With sorting on, Qt re-sorts after every setItem and the new row
        # moves away mid-fill, scattering its cells across rows.
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        try:
            row = table.rowCount()
            table.insertRow(row)
            for col, cell in enumerate(cells):
                table.setItem(row, col, cell)
        finally:
            table.setSortingEnabled(sorting)
        hold_numeric_columns(table, _NUMBER_COLUMNS)

    def _refresh_totals(self) -> None:
        total = combine(self._measured)
        self.known_card.set_value(_pct(total.known_pct, 1))
        self.new_words_card.set_value(f"{len(total.new_words):,}")
        self.i0_card.set_value(_pct(total.line_pct(I_PLUS_0), 0))
        self.i1_card.set_value(_pct(total.line_pct(I_PLUS_1), 0))

    def _clear_report(self) -> None:
        self._measured = []
        self.files_table.setRowCount(0)
        for card in (self.known_card, self.new_words_card, self.i0_card, self.i1_card):
            card.set_value(_NO_VALUE)
        self.report_card.hide()

"""Analytics tab for mining statistics, difficulty ranking, and progress tracking."""

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QFont, QHelpEvent, QPainter, QPalette
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.content_text import apply_content_direction, content_cell_font
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
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
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    ScreenIssueHost,
    configure_card_layout,
    configure_scrolled_page,
)
from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader, StatCard
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import config_language, get_profile, language_display_name
from anki_miner.models.stats import (
    DifficultyEntry,
    HistoryGrouping,
    KnownSizePoint,
    Milestone,
    MilestoneKind,
    MiningSession,
    OverallStats,
    SessionGroup,
)
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.frequency.coverage import (
    CoverageBand,
    FrequencyCoverage,
    measure_coverage,
    open_first_numeric_source,
)
from anki_miner.services.known_word_db import KnownWordDB, collect_knowledge_forms
from anki_miner.services.run_history import RunNotesCheck, check_run_notes, delete_run
from anki_miner.services.stats_service import StatsService, export_csv, group_sessions, sessions_since
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary

#: Rows a populated analytics table must show before it reads as "a table".
MIN_VISIBLE_ROWS = 6

#: Recent Sessions: date, series, episode, words, new words, cards.
_SESSION_COUNT_COLUMNS = (3, 4, 5)
#: The date needs one width and keeps it; only the two names should stretch.
_SESSION_FIT_COLUMNS = (0, *_SESSION_COUNT_COLUMNS)

#: Series Difficulty: rank, series, avg words, avg unknown, difficulty share.
_DIFFICULTY_COUNT_COLUMNS = (0, 2, 3, 4)
_DIFFICULTY_FIT_COLUMNS = _DIFFICULTY_COUNT_COLUMNS

#: The cells carrying MINED content, which is what the language face is for.
_SESSION_NAME_COLUMNS = (1, 2)
_DIFFICULTY_NAME_COLUMNS = (1,)

#: Column 0 of each history row carries its stats.db session id (AI-16).
#: UserRole is free: make_table_item keeps sort and copy values on +1 and +2.
_SESSION_ID_ROLE = Qt.ItemDataRole.UserRole

#: Mining History, grouped: period, sessions, words, new words, cards.
_GROUP_COUNT_COLUMNS = (1, 2, 3, 4)

#: Frequency Coverage: rank band, known, not known, known share.
_COVERAGE_COUNT_COLUMNS = (1, 2, 3)


def _count(value: int) -> SortableTableWidgetItem:
    """Build a count cell: grouped for reading, sorted on the number itself.

    Grouping matches the dashboard cards above the table, and it is exactly why
    the sort value has to be the integer -- "1,200" sorts before "900" as text.
    """
    return make_table_item(f"{value:,}", CellRole.NUMBER, sort_value=value)


def _japanese(item: SortableTableWidgetItem, style: ContentTextStyle) -> SortableTableWidgetItem:
    """Give a name cell the mining language's face, and nothing else.

    Series and episode names are usually written in the mined language, and this
    app also ships Simplified and Traditional Chinese interfaces, which prefer
    different shapes for the same characters (decision D45-B). The cell font
    carries no size, so it resolves against the view's own and the row height
    does not move.
    """
    item.setFont(content_cell_font(style))
    return item


def _apply_height_floor(table: QTableWidget) -> None:
    """Give ``table`` a minimum height measured in rows, not in pixels.

    Both tables used to sit under a flat 200px floor while their rows grew with
    the text scale, so at 150% the "table" showed 0.78 rows of 20 (Issue #102's
    class). A floor only means anything relative to the rows it holds, so it is
    derived from the same row height the shared data surface applies.
    """
    header = table.horizontalHeader()
    header_h = header.sizeHint().height() if header is not None else 0
    frame = 2 * table.frameWidth()
    table.setMinimumHeight(header_h + frame + MIN_VISIBLE_ROWS * data_row_height(table))


@dataclass(frozen=True)
class _AnalyticsBundle:
    """Pre-fetched analytics data assembled off the GUI thread.

    Carries every query result so :meth:`AnalyticsTab._apply_bundle` can render
    on the GUI thread without touching the (worker-thread) stats service.
    """

    stats: OverallStats
    sessions: list[MiningSession]
    difficulties: list[DifficultyEntry]
    milestones: list[Milestone]
    #: ST-05: the known-words counts recorded so far, shown until this visit's
    #: Anki scan lands.
    known_history: list[KnownSizePoint] = field(default_factory=list)


#: Known Words growth compares the latest count with the earliest one in this window (ST-05).
_GROWTH_WINDOW_DAYS = 30


@dataclass(frozen=True)
class _KnowledgeBundle:
    """The known-words count and frequency coverage, read off the GUI thread (ST-05, ST-07).

    ``known_count`` is ``None`` when Anki was not reachable; ``history`` still
    holds the counts recorded before. ``coverage`` is ``None`` without a
    numeric frequency source, or without Anki.
    """

    language: str
    known_count: int | None
    history: list[KnownSizePoint]
    coverage: FrequencyCoverage | None
    has_numeric_source: bool
    anki_error: str = ""


def _read_known_forms(config: AnkiMinerConfig) -> tuple[set[str], Callable[[str], str]]:
    """The known set Readability measures against, and the fold its DB rows use.

    A mirror of ``ReadabilityWorker._process_queue``'s recipe: Anki's card
    fronts with ``allow_degraded=False`` (an empty set on a timeout would read
    as "nothing known"), plus the user list and, when it is on, the
    known-words DB, through ``collect_knowledge_forms`` (F-01): with Word
    Filters → "Count a word as known only after its card has been reviewed"
    on, only reviewed cards count, and a failed review query falls back to
    every card. Read-only, like Readability.

    Raises:
        AnkiConnectionError: Anki is not reachable.
    """
    anki = AnkiService(config)
    vocabulary = anki.get_existing_vocabulary(allow_degraded=False)
    known_word_db = KnownWordDB(resolve_known_words_db_path(config), language=config_language(config))
    known = collect_knowledge_forms(known_word_db, config, vocabulary, anki.get_reviewed_vocabulary)
    return known, known_word_db.normalize_key


def _fetch_knowledge(config: AnkiMinerConfig, service: StatsService) -> _KnowledgeBundle:
    """Worker thread: count the known words, record today's count, measure coverage."""
    language = config_language(config)
    provider = open_first_numeric_source(config)
    try:
        try:
            known, fold = _read_known_forms(config)
        except AnkiConnectionError as exc:
            # Anki simply not running is expected: one WARNING line, no traceback.
            logging.getLogger(__name__).warning("Analytics: Anki not reachable for the known-words count: %s", exc)
            return _KnowledgeBundle(
                language=language,
                known_count=None,
                history=service.get_known_size_history(),
                coverage=None,
                has_numeric_source=provider is not None,
                anki_error=str(exc),
            )
        # The stats service follows the language on config_refreshed; a count
        # for one language must never land under another's row.
        if service.language == language:
            service.record_known_size(len(known))
        return _KnowledgeBundle(
            language=language,
            known_count=len(known),
            history=service.get_known_size_history(),
            coverage=measure_coverage(provider, known, fold) if provider is not None else None,
            has_numeric_source=provider is not None,
        )
    finally:
        if provider is not None:
            provider.close()


def _growth_reference(history: list[KnownSizePoint], *, today: date) -> KnownSizePoint | None:
    """The count the latest one is compared with: the earliest in the window, else the latest before it."""
    if len(history) < 2:
        return None
    earlier = history[:-1]
    window_start = today - timedelta(days=_GROWTH_WINDOW_DAYS)
    in_window = [point for point in earlier if point.day >= window_start]
    return in_window[0] if in_window else earlier[-1]


class _BarChart(QWidget):
    """Cards per period as plain bars (ST-02).

    AM ships no chart library and one bar series does not need one: a QPainter
    fill per bar in the palette's highlight colour (a theme routes the whole
    palette), the largest value at the top left, the first and last period
    under the bars, and each bar's period and count in its tooltip.
    """

    _HEIGHT = 150

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bars: list[tuple[str, int]] = []
        self.setMinimumHeight(self._HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(self.tr("Cards per period"))

    def sizeHint(self) -> QSize:
        return QSize(400, self._HEIGHT)

    def set_bars(self, bars: list[tuple[str, int]]) -> None:
        """Show ``bars`` as (period, cards), oldest first."""
        self._bars = list(bars)
        self.update()

    def bars(self) -> list[tuple[str, int]]:
        """The (period, cards) pairs on show, oldest first."""
        return list(self._bars)

    def _plot_rect(self) -> QRectF:
        """The area the bars stand in: one text line kept free above and below."""
        line = float(self.fontMetrics().height())
        return QRectF(4.0, line + 4.0, max(1.0, self.width() - 8.0), max(1.0, self.height() - 2.0 * line - 12.0))

    def bar_rects(self) -> list[QRectF]:
        """One rect per bar, left to right, scaled so the largest fills the plot."""
        if not self._bars:
            return []
        plot = self._plot_rect()
        peak = max(value for _label, value in self._bars) or 1
        slot = plot.width() / len(self._bars)
        width = max(1.0, slot * 0.7)
        rects: list[QRectF] = []
        for index, (_label, value) in enumerate(self._bars):
            height = plot.height() * value / peak
            left = plot.left() + index * slot + (slot - width) / 2
            rects.append(QRectF(left, plot.bottom() - height, width, height))
        return rects

    def bar_at(self, x: float) -> int | None:
        """The index of the bar whose slot holds ``x``, or ``None`` outside the plot."""
        if not self._bars:
            return None
        plot = self._plot_rect()
        if x < plot.left() or x >= plot.right():
            return None
        index = int((x - plot.left()) / (plot.width() / len(self._bars)))
        return min(index, len(self._bars) - 1)

    def paintEvent(self, a0) -> None:
        painter = QPainter(self)
        try:
            palette = self.palette()
            for rect in self.bar_rects():
                painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight))
            if not self._bars:
                return
            metrics = self.fontMetrics()
            plot = self._plot_rect()
            painter.setPen(palette.color(QPalette.ColorRole.WindowText))
            peak = max(value for _label, value in self._bars)
            painter.drawText(QPointF(plot.left(), float(metrics.ascent())), f"{peak:,}")
            baseline = float(self.height() - metrics.descent() - 2)
            painter.drawText(QPointF(plot.left(), baseline), self._bars[0][0])
            last = self._bars[-1][0]
            painter.drawText(QPointF(plot.right() - metrics.horizontalAdvance(last), baseline), last)
        finally:
            painter.end()

    def event(self, a0) -> bool:
        if a0 is not None and a0.type() == QEvent.Type.ToolTip and isinstance(a0, QHelpEvent):
            index = self.bar_at(float(a0.pos().x()))
            if index is None:
                QToolTip.hideText()
                a0.ignore()
            else:
                label, value = self._bars[index]
                QToolTip.showText(a0.globalPos(), f"{label}: {value:,}", self)
            return True
        return super().event(a0)


class _MissingWordsDialog(EnhancedDialog):
    """The words of one rank band the user does not know yet, rank first (ST-07).

    Read-only but selectable, so the list can be copied into a study tool.
    """

    def __init__(self, parent: QWidget | None, band: CoverageBand, source_name: str, style: ContentTextStyle) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Missing Words"))
        self.set_header("", tr_format(self.tr("Not known yet in ranks %1"), f"{band.low:,}–{band.high:,}"), source_name)
        self.words_view = QPlainTextEdit()
        self.words_view.setReadOnly(True)
        # Mined content: the mining language's face and direction (D45-B, S21).
        self.words_view.setFont(content_cell_font(style))
        apply_content_direction(self.words_view, style)
        self.words_view.setPlainText("\n".join(f"{rank}\t{word}" for rank, word in band.missing))
        self.add_content(self.words_view, 1)
        self.add_close_button()


class AnalyticsTab(ScreenIssueHost, QWidget):
    """Tab displaying mining analytics, difficulty rankings, and milestones."""

    #: Tables and queue rows genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    # showEvent fires on every tab switch. Skip the refresh if data is fresh
    # within this window so rapid tab clicking stays snappy.
    _REFRESH_TTL_SECONDS = 5.0

    # ST-05/ST-07: the Anki scan behind Known Words and Frequency Coverage is
    # the cost of a Readability check, so it runs at most this often unless a
    # run finished (mark_stale) or the mining language changed.
    _KNOWLEDGE_TTL_SECONDS = 600.0

    def __init__(
        self,
        stats_service: StatsService,
        parent=None,
        *,
        content_style: ContentTextStyle | None = None,
        config_provider: Callable[[], AnkiMinerConfig] | None = None,
    ):
        super().__init__(parent)
        self.stats_service = stats_service
        # Series and episode names are mined content: the face comes from the
        # mining language's profile. None keeps today's Japanese face.
        self._content_style = content_style or get_profile("ja").content_style
        self._last_refresh: float | None = None
        # Guard against overlapping off-thread refreshes stacking up (a fast
        # tab switch fires showEvent repeatedly). Cleared in on_done/on_error.
        self._refresh_in_flight: bool = False
        # A run finished while a read was in flight: that read may predate the
        # run's rows, so _on_refresh_done reads again instead of ticking the TTL.
        self._stale_pending: bool = False
        # Reset and refresh are strictly serialised against each other: a refresh
        # that read the tables before the delete landed would otherwise render its
        # pre-delete snapshot *after* the reset finished, leaving the tab showing
        # numbers that no longer exist. A refresh disarms the reset button while it
        # runs, and this flag makes refresh_data a no-op while a reset runs, so the
        # two can never overlap in either direction.
        self._reset_in_flight: bool = False
        # Bumped in shutdown() so a refresh/reset callback already queued for
        # delivery when app close begins finds itself stale and never touches
        # a table or button the close may be tearing down (M8). Analytics is
        # added directly to the main QTabWidget, so
        # BackgroundTaskController.shutdown calls this like every other
        # top-level tab that exposes the hook.
        self._teardown_generation = 0
        # AI-16: the live config (AnkiConnect URL, mining language, known-words
        # path), read at the moment of each action. None (tests, tools that
        # embed the tab without a window) leaves the run actions off.
        self._config_provider = config_provider
        # The rows on screen, by session id, for the run actions.
        self._sessions_by_id: dict[int, MiningSession] = {}
        # One run action at a time: a second click would race the first delete.
        self._run_action_in_flight = False
        # ST-02: the whole history as last read; range and grouping re-cut it
        # on the GUI thread without another query.
        self._all_sessions: list[MiningSession] = []
        # The rows of the table on show, for Export CSV (L2.ST02.T4).
        self._history_export: list[list[object]] = []
        # ST-05/ST-07: one off-thread scan feeds both sections, only while the
        # page is on screen (a hidden tab never reaches into Anki).
        self._knowledge_in_flight = False
        self._knowledge_at: float | None = None
        self._has_history = False
        # ST-07: the last coverage shown, and whether the language has a numeric list.
        self._coverage: FrequencyCoverage | None = None
        self._coverage_available = False
        self._missing_dialog: _MissingWordsDialog | None = None
        self._setup_ui()
        self._setup_accessibility()

    def set_content_style(self, style: ContentTextStyle) -> None:
        """Re-point the name cells at *style* after a language switch (spec 6.1).

        The style is captured at construction, so without this the tab keeps
        rendering series and episode names in the outgoing language's shapes
        until the next launch. The rows already on screen are re-faced in place:
        the data has not changed, only what it should look like.
        """
        # A language switch: the next visit counts the new language's words.
        self._knowledge_at = None
        self._content_style = style
        font = content_cell_font(style)
        for table, columns in (
            (self.sessions_table, _SESSION_NAME_COLUMNS),
            (self.difficulty_table, _DIFFICULTY_NAME_COLUMNS),
        ):
            for row in range(table.rowCount()):
                for column in columns:
                    item = table.item(row, column)
                    if item is not None:
                        item.setFont(font)

    def shutdown(self) -> None:
        """Invalidate in-flight refresh/reset callbacks before app close."""
        # getattr: test doubles that subclass this tab and skip its __init__
        # never set the attribute.
        self._teardown_generation = getattr(self, "_teardown_generation", 0) + 1

    def _setup_ui(self) -> None:
        scroll_area = QScrollArea()

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # E09: with nothing recorded the page is this one line, not four cards
        # of zeros and empty tables.
        self.empty_state_label = QLabel(self.tr("No mining yet — your statistics appear here after your first run."))
        self.empty_state_label.setObjectName("section-header")
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.hide()
        layout.addWidget(self.empty_state_label)

        # Section 1: Overview Dashboard
        self._dashboard_card = self._create_dashboard_section()
        layout.addWidget(self._dashboard_card)

        # Section 2: Recent Sessions
        self._sessions_card = self._create_recent_sessions_section()
        layout.addWidget(self._sessions_card)

        # Section 3: Series Difficulty
        self._difficulty_card = self._create_difficulty_section()
        layout.addWidget(self._difficulty_card)

        # Section: Frequency Coverage (ST-07), only when the language has a numeric list.
        self._coverage_card = self._create_coverage_section()
        layout.addWidget(self._coverage_card)

        # Section 4: Milestones
        self._milestones_card = self._create_milestones_section()
        layout.addWidget(self._milestones_card)

        # Reset stays, far left. There is no Refresh: a finished run marks this
        # page stale (mark_stale) and showing it re-reads it (E09).
        button_layout = QHBoxLayout()
        self.reset_button = ModernButton(self.tr("Reset Statistics…"), variant="critical")
        self.reset_button.setToolTip(
            self.tr(
                "Delete the mining sessions and difficulty scores recorded for the current "
                "mining language. This cannot be undone."
            )
        )
        # Armed only once a refresh proves there is something to delete, so an
        # empty database never raises a confirmation with nothing behind it.
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self._on_reset_clicked)
        button_layout.addWidget(self.reset_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        layout.addStretch()

        container.setLayout(layout)
        configure_scrolled_page(scroll_area, container, self.PAGE_WIDTH)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)
        # Above the numbers, because the numbers are what went stale (D24).
        self.install_issue_banner(main_layout)

    def changeEvent(self, a0):  # noqa: N802  (Qt override)
        """Re-derive table row metrics whenever the font changes.

        Zoom is applied live (Settings -> General), so a row height computed once
        at construction goes stale the moment the user changes it -- which is the
        same "pixel constant frozen against a font that later grows" mistake the
        row sizing exists to fix.
        """
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.FontChange:
            for table in (
                getattr(self, "sessions_table", None),
                getattr(self, "difficulty_table", None),
                getattr(self, "history_groups_table", None),
                getattr(self, "coverage_table", None),
            ):
                if table is not None:
                    configure_data_view(table)
                    _apply_height_floor(table)

    def _setup_accessibility(self) -> None:
        self.setAccessibleName(self.tr("Analytics Tab"))
        self.setAccessibleDescription(
            self.tr("View mining statistics, series difficulty rankings, and progress milestones")
        )

    def _create_dashboard_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        header = SectionHeader(self.tr("Overview"))
        layout.addWidget(header)

        grid = QGridLayout()
        grid.setSpacing(SPACING.sm)

        self.card_total_cards = StatCard(value="0", label=self.tr("Total Cards"))
        self.card_total_sessions = StatCard(value="0", label=self.tr("Sessions"))
        self.card_total_series = StatCard(value="0", label=self.tr("Series Mined"))

        grid.addWidget(self.card_total_cards, 0, 0)
        grid.addWidget(self.card_total_sessions, 0, 1)
        grid.addWidget(self.card_total_series, 0, 2)

        # ST-05: the label says what is counted (R11); the caption carries
        # the language and the change.
        self.card_known_words = StatCard(value="0", label=self.tr("Known Words"))
        self.card_known_words.setToolTip(self.tr("Words Anki Miner treats as known in the current mining language."))
        self.card_known_words.hide()
        grid.addWidget(self.card_known_words, 0, 3)

        layout.addLayout(grid)
        self.known_words_caption = QLabel()
        self.known_words_caption.setObjectName("helper-text")
        self.known_words_caption.setWordWrap(True)
        self.known_words_caption.hide()
        layout.addWidget(self.known_words_caption)
        group.setLayout(layout)
        return group

    def _create_recent_sessions_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        header = SectionHeader(self.tr("Mining History"))
        layout.addWidget(header)

        # ST-02: a range and a grouping over the whole history. Both are
        # transient (no setting): every launch opens on all runs, one by one.
        controls = QHBoxLayout()
        controls.setSpacing(SPACING.sm)
        self.history_range_combo = QComboBox()
        self.history_range_combo.setAccessibleName(self.tr("History period"))
        for text, days in (
            (self.tr("All time"), 0),
            (self.tr("Last 7 days"), 7),
            (self.tr("Last 30 days"), 30),
            (self.tr("Last 12 months"), 365),
        ):
            self.history_range_combo.addItem(text, days)
        controls.addWidget(self.history_range_combo)
        self.history_group_combo = QComboBox()
        self.history_group_combo.setAccessibleName(self.tr("History grouping"))
        for text, grouping in (
            (self.tr("Each run"), HistoryGrouping.RUN),
            (self.tr("By day"), HistoryGrouping.DAY),
            (self.tr("By week"), HistoryGrouping.WEEK),
            (self.tr("By month"), HistoryGrouping.MONTH),
        ):
            self.history_group_combo.addItem(text, grouping.value)
        controls.addWidget(self.history_group_combo)
        controls.addStretch(1)
        self.export_history_button = ModernButton(self.tr("Export CSV…"), variant="secondary")
        self.export_history_button.setEnabled(False)
        self.export_history_button.clicked.connect(self._on_export_history_clicked)
        controls.addWidget(self.export_history_button)
        layout.addLayout(controls)
        self.history_export_note = QLabel()
        self.history_export_note.setObjectName("helper-text")
        self.history_export_note.hide()
        layout.addWidget(self.history_export_note)

        self.history_chart = _BarChart()
        self.history_chart.hide()
        layout.addWidget(self.history_chart)

        self.sessions_empty_label = QLabel(self.tr("No sessions yet — process an episode to see your history."))
        self.sessions_empty_label.setObjectName("helper-text")
        self.sessions_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sessions_empty_label.setMinimumHeight(80)
        layout.addWidget(self.sessions_empty_label)

        self.sessions_table = QTableWidget()
        self.sessions_table.setColumnCount(6)
        self.sessions_table.setHorizontalHeaderLabels(
            [
                self.tr("Date"),
                self.tr("Series"),
                self.tr("Episode"),
                self.tr("Words"),
                self.tr("New Words"),
                self.tr("Cards"),
            ]
        )
        configure_table_header(self.sessions_table, fit_columns=_SESSION_FIT_COLUMNS)
        self.sessions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sessions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sessions_table.setSortingEnabled(True)
        configure_data_view(self.sessions_table)
        install_copy_rows(self.sessions_table)
        _apply_height_floor(self.sessions_table)
        self.sessions_table.hide()
        layout.addWidget(self.sessions_table)

        # The grouped view: one row per period, the same counts summed.
        self.history_groups_table = QTableWidget()
        self.history_groups_table.setColumnCount(5)
        self.history_groups_table.setHorizontalHeaderLabels(
            [self.tr("Period"), self.tr("Sessions"), self.tr("Words"), self.tr("New Words"), self.tr("Cards")]
        )
        configure_table_header(self.history_groups_table, fit_columns=(0, *_GROUP_COUNT_COLUMNS))
        self.history_groups_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_groups_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_groups_table.setSortingEnabled(True)
        configure_data_view(self.history_groups_table)
        install_copy_rows(self.history_groups_table)
        _apply_height_floor(self.history_groups_table)
        self.history_groups_table.hide()
        layout.addWidget(self.history_groups_table)

        # AI-16: act on the one selected run. Both actions run off the GUI
        # thread; a failure lands in the screen banner (D24).
        self.run_actions_row = QWidget()
        actions = QHBoxLayout(self.run_actions_row)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(SPACING.sm)
        self.show_in_anki_button = ModernButton(self.tr("Show in Anki"), variant="secondary")
        self.show_in_anki_button.clicked.connect(self._on_show_in_anki_clicked)
        actions.addWidget(self.show_in_anki_button)
        self.delete_run_button = ModernButton(self.tr("Delete this run's cards…"), variant="critical")
        self.delete_run_button.clicked.connect(self._on_delete_run_clicked)
        actions.addWidget(self.delete_run_button)
        self.run_action_note = QLabel()
        self.run_action_note.setObjectName("helper-text")
        self.run_action_note.setWordWrap(True)
        self.run_action_note.hide()
        actions.addWidget(self.run_action_note, 1)
        self.run_actions_row.hide()
        layout.addWidget(self.run_actions_row)
        self.sessions_table.itemSelectionChanged.connect(self._sync_run_actions)
        self._sync_run_actions()

        # Connected last: addItem above already moved the current index, and
        # a render before the tables exist would fail.
        self.history_range_combo.currentIndexChanged.connect(self._render_history)
        self.history_group_combo.currentIndexChanged.connect(self._render_history)

        group.setLayout(layout)
        return group

    def _create_difficulty_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        header = SectionHeader(self.tr("Series Difficulty Ranking"))
        layout.addWidget(header)

        explanation = QLabel(self.tr("Share of unknown words per series — lower means easier for your current level."))
        explanation.setWordWrap(True)
        explanation.setObjectName("helper-text")
        layout.addWidget(explanation)

        self.difficulty_empty_label = QLabel(self.tr("Mine multiple series to see difficulty comparisons."))
        self.difficulty_empty_label.setObjectName("helper-text")
        self.difficulty_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.difficulty_empty_label.setMinimumHeight(80)
        layout.addWidget(self.difficulty_empty_label)

        self.difficulty_table = QTableWidget()
        self.difficulty_table.setColumnCount(5)
        self.difficulty_table.setHorizontalHeaderLabels(
            [self.tr("Rank"), self.tr("Series"), self.tr("Avg Words"), self.tr("Avg Unknown"), self.tr("Difficulty")]
        )
        configure_table_header(self.difficulty_table, fit_columns=_DIFFICULTY_FIT_COLUMNS)
        self.difficulty_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.difficulty_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.difficulty_table.setSortingEnabled(True)
        configure_data_view(self.difficulty_table)
        install_copy_rows(self.difficulty_table)

        _apply_height_floor(self.difficulty_table)
        self.difficulty_table.hide()

        layout.addWidget(self.difficulty_table)
        group.setLayout(layout)
        return group

    def _create_coverage_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self.tr("Frequency Coverage")))

        self.coverage_source_label = QLabel()
        self.coverage_source_label.setObjectName("helper-text")
        self.coverage_source_label.setWordWrap(True)
        layout.addWidget(self.coverage_source_label)

        self.coverage_status_label = QLabel()
        self.coverage_status_label.setObjectName("helper-text")
        self.coverage_status_label.setWordWrap(True)
        self.coverage_status_label.hide()
        layout.addWidget(self.coverage_status_label)

        self.coverage_table = QTableWidget()
        self.coverage_table.setColumnCount(4)
        self.coverage_table.setHorizontalHeaderLabels(
            [self.tr("Rank band"), self.tr("Known"), self.tr("Not known"), self.tr("Known %")]
        )
        configure_table_header(self.coverage_table, fit_columns=(0, *_COVERAGE_COUNT_COLUMNS))
        self.coverage_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.coverage_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.coverage_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        # No sorting: the bands read in rank order, and a row's index is its band's.
        configure_data_view(self.coverage_table)
        install_copy_rows(self.coverage_table)
        _apply_height_floor(self.coverage_table)
        self.coverage_table.itemSelectionChanged.connect(self._sync_missing_button)
        layout.addWidget(self.coverage_table)

        buttons = QHBoxLayout()
        self.show_missing_button = ModernButton(self.tr("Show missing words"), variant="secondary")
        self.show_missing_button.setEnabled(False)
        self.show_missing_button.clicked.connect(self._on_show_missing_clicked)
        buttons.addWidget(self.show_missing_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        group.setLayout(layout)
        group.hide()
        return group

    def _create_milestones_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        header = SectionHeader(self.tr("Milestones"))
        layout.addWidget(header)

        self.milestones_layout = QVBoxLayout()
        self.milestones_layout.setSpacing(SPACING.xs)
        layout.addLayout(self.milestones_layout)

        group.setLayout(layout)
        return group

    def refresh_data(self, force: bool = False) -> None:
        """Refresh all analytics data from the stats service.

        The four SQLite queries run off the GUI thread (OVH); widget updates
        happen back on the GUI thread once they complete.

        Args:
            force: Skip the staleness check. ``mark_stale``, a reset and the
                banner's Retry pass ``force=True``; ``showEvent`` does not.
        """
        if not self.stats_service.is_available():
            return

        # A reset owns the tables until it finishes, and ends by forcing its own
        # refresh. Reading here would only race the delete.
        if self._reset_in_flight:
            return

        if (
            not force
            and self._last_refresh is not None
            and time.monotonic() - self._last_refresh < self._REFRESH_TTL_SECONDS
        ):
            return

        # In-flight guard: overlapping refreshes (rapid tab switching, or a
        # mark_stale while a showEvent refresh is still running) would stack
        # redundant SQLite work and racing renders.
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        # No reset over a half-rendered table; _apply_bundle re-arms it.
        self.reset_button.setEnabled(False)

        service = self.stats_service

        def _fetch() -> _AnalyticsBundle:
            # Worker thread: touch ONLY the stats service / sqlite, never widgets.
            stats = service.get_overall_stats()
            return _AnalyticsBundle(
                stats=stats,
                # ST-02: the whole history; the range combo cuts it on screen.
                sessions=service.get_recent_sessions(limit=None),
                difficulties=service.get_series_difficulty(),
                milestones=service.get_milestones(stats=stats),
                known_history=service.get_known_size_history(),
            )

        run_off_thread(
            self,
            _fetch,
            self._on_refresh_done,
            self._on_refresh_error,
        )

    def mark_stale(self) -> None:
        """A run just finished, so these numbers are out of date (E09).

        Re-read at once when the page is on screen; otherwise the freshness
        clock is cleared, so the next visit's ``showEvent`` re-reads it. A read
        already in flight may predate the run, so it re-runs when it lands.
        """
        # A run may have added known words: count again on this or the next visit.
        self._knowledge_at = None
        self._last_refresh = None
        if self._refresh_in_flight:
            self._stale_pending = True
            return
        if self.isVisible():
            self.refresh_data(force=True)

    def _on_refresh_done(self, bundle: object) -> None:
        """GUI thread: render the pre-fetched bundle and tick the TTL clock."""
        self._refresh_in_flight = False
        if self._teardown_generation:
            return
        # Tab torn down while the fetch was in flight (its C++ widgets are
        # gone); the queued callback has nothing live to render.
        with contextlib.suppress(RuntimeError):
            # Success is the only thing that clears a refresh issue (D24).
            self.clear_screen_issue()
            if not isinstance(bundle, _AnalyticsBundle):  # defensive; never expected
                return
            self._apply_bundle(bundle)
            self._maybe_refresh_knowledge()
            if self._stale_pending:
                self._stale_pending = False
                self.mark_stale()
            else:
                self._last_refresh = time.monotonic()

    def _on_refresh_error(self, msg: str) -> None:
        """GUI thread: clear the in-flight flag, log, and say so on screen.

        The whole tab *is* the fetch, so a failure that only reached the log
        left the user reading stale numbers with no way to tell (D24). The
        in-flight flag is cleared first on purpose: the banner offers Retry,
        and Retry is a ``refresh_data`` call the guard would otherwise swallow.
        """
        self._refresh_in_flight = False
        # The clock is still clear, so the next visit or Retry reads anyway.
        self._stale_pending = False
        logging.getLogger(__name__).error("Failed to refresh analytics data: %s", msg)
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Analytics could not be refreshed."),
                    details=msg,
                    action_id="analytics.retry",
                    action_text=self.tr("Retry"),
                ),
                action=lambda: self.refresh_data(force=True),
            )

    def _on_reset_clicked(self) -> None:
        """Confirm, then wipe both stats tables off the GUI thread.

        The emptied tab is the receipt -- no success modal, the same way
        ``KnownWordsManagerDialog._on_reset`` just redraws its list.
        """
        confirm = QMessageBox.question(
            self,
            self.tr("Reset Statistics"),
            tr_format(
                self.tr(
                    "Delete every mining session and series difficulty score recorded for %1? "
                    "This cannot be undone. Statistics for your other mining languages, "
                    "your Anki cards, known words, and settings are not affected."
                ),
                language_display_name(str(self.stats_service.language)),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        log_summary(
            logging.getLogger(__name__),
            "Confirm",
            action="reset_statistics",
            answer="yes" if confirm == QMessageBox.StandardButton.Yes else "no",
            scope=self.stats_service.language,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._reset_in_flight = True
        self.reset_button.setEnabled(False)
        service = self.stats_service
        run_off_thread(self, service.reset, self._on_reset_done, self._on_reset_error)

    def _on_reset_done(self, _removed: object) -> None:
        """GUI thread: re-read the now-empty tables so the tab shows the result."""
        self._reset_in_flight = False
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.clear_screen_issue()
            # force=True: the TTL would otherwise swallow the one refresh that matters.
            self.refresh_data(force=True)

    def _on_reset_error(self, msg: str) -> None:
        """GUI thread: re-arm both buttons and say so on screen (D24)."""
        self._reset_in_flight = False
        logging.getLogger(__name__).error("Failed to reset analytics data: %s", msg)
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.reset_button.setEnabled(True)
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Statistics could not be reset."),
                    details=msg,
                )
            )

    def _apply_bundle(self, bundle: _AnalyticsBundle) -> None:
        """Render every section from a pre-fetched bundle (GUI thread)."""
        empty = bundle.stats.total_sessions == 0 and not bundle.difficulties
        # Nothing recorded: one line instead of the sections, and nothing to
        # reset (E09).
        self.empty_state_label.setVisible(empty)
        for card in (self._dashboard_card, self._sessions_card, self._difficulty_card, self._milestones_card):
            card.setVisible(not empty)
        self.reset_button.setVisible(not empty)
        # Nothing recorded means nothing to reset: leave the button disabled
        # rather than raise a confirmation over an empty database.
        self.reset_button.setEnabled(not empty)
        self._has_history = not empty
        self._coverage_card.setVisible(not empty and self._coverage_available)
        if self._knowledge_at is None:
            # Until this visit's scan lands, the last recorded count stands in.
            self._render_known_words(
                str(self.stats_service.language), None, list(bundle.known_history), anki_down=False
            )
        self._update_dashboard(bundle.stats)
        self._all_sessions = list(bundle.sessions)
        self._render_history()
        self._update_difficulty_ranking(bundle.difficulties)
        self._update_milestones(bundle.milestones)

    def _update_dashboard(self, stats) -> None:
        self.card_total_cards.set_value(f"{stats.total_cards_created:,}")
        self.card_total_sessions.set_value(str(stats.total_sessions))
        self.card_total_series.set_value(str(stats.series_count))

    def _update_recent_sessions(self, sessions: list[MiningSession]) -> None:
        has_sessions = len(sessions) > 0
        self.sessions_table.setVisible(has_sessions)
        self.sessions_empty_label.setVisible(not has_sessions)
        self._sessions_by_id = {session.id: session for session in sessions if session.id is not None}
        self.run_actions_row.setVisible(has_sessions and self._config_provider is not None)

        self.sessions_table.setUpdatesEnabled(False)
        was_sorting = self.sessions_table.isSortingEnabled()
        self.sessions_table.setSortingEnabled(False)
        try:
            self.sessions_table.setRowCount(len(sessions))
            for row_idx, session in enumerate(sessions):
                items = [
                    # The date sorts by its instant: the printed form is a
                    # string, and a string sorts by its first character.
                    make_table_item(
                        session.mined_at.strftime("%Y-%m-%d %H:%M"),
                        sort_value=session.mined_at.timestamp(),
                    ),
                    _japanese(make_table_item(session.series_name), self._content_style),
                    _japanese(make_table_item(session.episode_name), self._content_style),
                    _count(session.total_words),
                    _count(session.unknown_words),
                    # Ruling C5: a run deleted here keeps its count and says so.
                    self._cards_cell(session),
                ]
                items[0].setData(_SESSION_ID_ROLE, session.id)
                for col_idx, item in enumerate(items):
                    self.sessions_table.setItem(row_idx, col_idx, item)
        finally:
            self.sessions_table.setSortingEnabled(was_sorting)
            self.sessions_table.setUpdatesEnabled(True)
        hold_numeric_columns(self.sessions_table, _SESSION_COUNT_COLUMNS)
        self._sync_run_actions()

    # === ST-02: range, grouping, chart ===

    def _history_since(self) -> datetime | None:
        """The start of the chosen range, or ``None`` for all time."""
        days = self.history_range_combo.currentData()
        if not isinstance(days, int) or days <= 0:
            return None
        return datetime.now() - timedelta(days=days)

    def _render_history(self, *_args: object) -> None:
        """Cut the cached history to the range and show it run by run or per period."""
        sessions = sessions_since(self._all_sessions, self._history_since())
        grouping = HistoryGrouping(self.history_group_combo.currentData())
        per_run = grouping is HistoryGrouping.RUN
        groups = [] if per_run else group_sessions(sessions, grouping)
        self._update_recent_sessions(sessions if per_run else [])
        self._update_history_groups(groups)
        # Oldest to newest; run by run, the chart counts cards per day.
        chart = group_sessions(sessions, HistoryGrouping.DAY) if per_run else groups
        self.history_chart.set_bars([(period.key, period.cards_created) for period in reversed(chart)])
        self.history_chart.setVisible(bool(chart))
        self.sessions_empty_label.setText(
            self.tr("No runs in this period.")
            if self._all_sessions
            else self.tr("No sessions yet — process an episode to see your history.")
        )
        self.sessions_empty_label.setVisible(not sessions)
        if per_run:
            self._history_export = [
                [
                    session.mined_at.strftime("%Y-%m-%d %H:%M"),
                    session.series_name,
                    session.episode_name,
                    session.total_words,
                    session.unknown_words,
                    session.cards_created,
                ]
                for session in sessions
            ]
        else:
            self._history_export = [
                [period.key, period.sessions, period.total_words, period.unknown_words, period.cards_created]
                for period in groups
            ]
        self.export_history_button.setEnabled(bool(self._history_export))

    def _update_history_groups(self, groups: list[SessionGroup]) -> None:
        """Fill the per-period table; hidden when there is nothing to group."""
        table = self.history_groups_table
        table.setVisible(bool(groups))
        table.setUpdatesEnabled(False)
        was_sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        try:
            table.setRowCount(len(groups))
            for row_idx, period in enumerate(groups):
                items = [
                    # Sorted by the period's first day: "2026-W9" must not sort after "2026-W10".
                    make_table_item(period.key, sort_value=period.start.toordinal()),
                    _count(period.sessions),
                    _count(period.total_words),
                    _count(period.unknown_words),
                    _count(period.cards_created),
                ]
                for col_idx, item in enumerate(items):
                    table.setItem(row_idx, col_idx, item)
        finally:
            table.setSortingEnabled(was_sorting)
            table.setUpdatesEnabled(True)
        hold_numeric_columns(table, _GROUP_COUNT_COLUMNS)

    def _history_header(self) -> list[str]:
        """The column titles of the table on show, as the CSV header."""
        per_run = HistoryGrouping(self.history_group_combo.currentData()) is HistoryGrouping.RUN
        table = self.sessions_table if per_run else self.history_groups_table
        titles: list[str] = []
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            titles.append(item.text() if item is not None else "")
        return titles

    def _on_export_history_clicked(self) -> None:
        """Ask where, then write the rows on show as CSV off the GUI thread (ST-02)."""
        header = self._history_header()
        rows = list(self._history_export)
        if not rows:
            return

        def _on_picked(path_str: str) -> None:
            if not path_str:
                return
            path = Path(path_str)
            run_off_thread(
                self,
                lambda: export_csv(path, header, rows),
                lambda _count: self._on_history_exported(path),
                self._on_history_export_error,
            )

        file_dialogs.pick_save_file(
            self,
            self.tr("Export Mining History"),
            str(Path(resolve_start_dir(None, file_mode=True)) / "mining_history.csv"),
            self.tr("CSV Files (*.csv);;All Files (*)"),
            on_done=_on_picked,
        )

    def _on_history_exported(self, path: Path) -> None:
        """GUI thread: a quiet line under the controls is the receipt."""
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.history_export_note.setText(tr_format(self.tr("History saved to %1."), path.name))
            self.history_export_note.show()

    def _on_history_export_error(self, msg: str) -> None:
        """GUI thread: report the failed write on screen (D24)."""
        logging.getLogger(__name__).warning("Analytics: history export failed: %s", msg)
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.show_screen_issue(ScreenIssue(summary=self.tr("The history could not be exported."), details=msg))

    # === AI-16: act on one past run ===

    def _selected_session(self) -> MiningSession | None:
        """The run whose row is selected, or ``None`` unless exactly one row is."""
        rows = {index.row() for index in self.sessions_table.selectedIndexes()}
        if len(rows) != 1:
            return None
        item = self.sessions_table.item(rows.pop(), 0)
        if item is None:
            return None
        session_id = item.data(_SESSION_ID_ROLE)
        return self._sessions_by_id.get(session_id) if isinstance(session_id, int) else None

    def _cards_cell(self, session: MiningSession) -> SortableTableWidgetItem:
        """The Cards cell. A run whose cards were deleted here is marked (ruling C5).

        The number stays what the run created: history totals are not
        rewritten. The mark adds how many of its cards are gone from Anki, and
        the column still sorts on the created count.
        """
        if session.deleted_count <= 0:
            return _count(session.cards_created)
        return make_table_item(
            tr_format(
                self.tr("%1 (cards deleted: %2)"),
                f"{session.cards_created:,}",
                f"{session.deleted_count:,}",
            ),
            CellRole.NUMBER,
            sort_value=session.cards_created,
        )

    def _sync_run_actions(self) -> None:
        """Arm Show in Anki and Delete only for a selected run that kept its card list."""
        session = self._selected_session()
        usable = (
            self._config_provider is not None
            and session is not None
            and session.note_count > 0
            and not self._run_action_in_flight
        )
        if session is None:
            tip = self.tr("Select one run in the table.")
        elif session.deleted_count > 0:
            tip = tr_format(self.tr("This run's cards were deleted: %1."), f"{session.deleted_count:,}")
        elif session.note_count == 0:
            tip = self.tr(
                "Anki Miner has no card list for this run: it was mined before this version, or it made no cards."
            )
        else:
            tip = ""
        for button in (self.show_in_anki_button, self.delete_run_button):
            button.setEnabled(usable)
            button.setToolTip(tip)

    def _on_show_in_anki_clicked(self) -> None:
        """Open Anki's browser on the selected run's notes (AI-16), off the GUI thread."""
        session = self._selected_session()
        if session is None or session.id is None or self._config_provider is None:
            return
        config = self._config_provider()
        service = self.stats_service
        session_id = session.id

        def _browse() -> int:
            # Worker thread: stats.db and AnkiConnect only, never a widget.
            receipt = service.get_run_receipt(session_id)
            note_ids = list(receipt.note_ids) if receipt is not None else []
            AnkiService(config).gui_browse_notes(note_ids)
            return len(note_ids)

        # Success is Anki's own window; this tab has nothing to change.
        run_off_thread(self, _browse, lambda _count: None, self._on_show_in_anki_error)

    def _on_show_in_anki_error(self, msg: str) -> None:
        """GUI thread: say on screen that Anki's browser could not be opened (D24)."""
        logging.getLogger(__name__).warning("Analytics: Show in Anki failed: %s", msg)
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.show_screen_issue(
                ScreenIssue(summary=self.tr("Anki Miner couldn't open this run's cards in Anki."), details=msg)
            )

    def _on_delete_run_clicked(self) -> None:
        """Ask Anki what is left of the selected run, then confirm (AI-16)."""
        session = self._selected_session()
        if session is None or session.id is None or self._config_provider is None or self._run_action_in_flight:
            return
        config = self._config_provider()
        service = self.stats_service
        session_id = session.id
        self._set_run_action_in_flight(True)
        self.run_action_note.hide()

        def _check() -> RunNotesCheck:
            # Worker thread: which recorded notes still exist, and how many were reviewed.
            receipt = service.get_run_receipt(session_id)
            return check_run_notes(AnkiService(config), receipt.note_ids if receipt is not None else ())

        run_off_thread(
            self,
            _check,
            lambda check: self._confirm_delete_run(session_id, config, check),
            self._on_delete_run_error,
        )

    def _confirm_delete_run(self, session_id: int, config: AnkiMinerConfig, check: object) -> None:
        """GUI thread: the last moment to say no, naming what would be lost."""
        self._set_run_action_in_flight(False)
        if self._teardown_generation or not isinstance(check, RunNotesCheck):
            return
        service = self.stats_service
        if not check.present:
            # Review Focus 4: Anki already lost every note (an Undo, or a delete
            # in Anki). Nothing to confirm; drop the list so the row disarms.
            self._show_run_note(self.tr("None of this run's cards are in Anki any more."))
            run_off_thread(
                self,
                lambda: service.forget_run_notes(session_id),
                lambda _result: self.refresh_data(force=True),
                self._on_delete_run_error,
            )
            return
        answer = QMessageBox.question(
            self,
            self.tr("Delete This Run's Cards"),
            tr_format(
                self.tr(
                    "Cards this run added that are still in Anki: %1. Reviewed at least once: %2.\n\n"
                    "Delete them from Anki? Their review history is deleted with them. This cannot be undone."
                ),
                f"{len(check.present):,}",
                f"{check.reviewed:,}",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        log_summary(
            logging.getLogger(__name__),
            "Confirm",
            action="delete_run_cards",
            answer="yes" if answer == QMessageBox.StandardButton.Yes else "no",
            cards=len(check.present),
            reviewed=check.reviewed,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_run_action_in_flight(True)

        def _known_words_db_for(code: str) -> KnownWordDB:
            # The run's own language: its mined rows live in that DB (Review Focus 5).
            return KnownWordDB(resolve_known_words_db_path(replace(config, language=code)), language=code)

        run_off_thread(
            self,
            lambda: delete_run(AnkiService(config), service, session_id, known_words_db_for=_known_words_db_for),
            self._on_delete_run_done,
            self._on_delete_run_error,
        )

    def _on_delete_run_done(self, deleted: object) -> None:
        """GUI thread: say what went, then re-read the page so the row disarms."""
        self._set_run_action_in_flight(False)
        if self._teardown_generation:
            return
        count = deleted if isinstance(deleted, int) else 0
        with contextlib.suppress(RuntimeError):
            self._show_run_note(tr_format(self.tr("Cards deleted from Anki: %1."), f"{count:,}"))
            self.refresh_data(force=True)

    def _on_delete_run_error(self, msg: str) -> None:
        """GUI thread: re-arm the row and report on screen (D24)."""
        self._set_run_action_in_flight(False)
        logging.getLogger(__name__).warning("Analytics: deleting a run's cards failed: %s", msg)
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.show_screen_issue(ScreenIssue(summary=self.tr("This run's cards could not be deleted."), details=msg))

    def _set_run_action_in_flight(self, in_flight: bool) -> None:
        self._run_action_in_flight = in_flight
        with contextlib.suppress(RuntimeError):
            self._sync_run_actions()

    def _show_run_note(self, text: str) -> None:
        self.run_action_note.setText(text)
        self.run_action_note.show()

    def _update_difficulty_ranking(self, difficulties: list[DifficultyEntry]) -> None:
        has_difficulties = len(difficulties) > 0
        self.difficulty_table.setVisible(has_difficulties)
        self.difficulty_empty_label.setVisible(not has_difficulties)

        self.difficulty_table.setUpdatesEnabled(False)
        was_sorting = self.difficulty_table.isSortingEnabled()
        self.difficulty_table.setSortingEnabled(False)
        try:
            self.difficulty_table.setRowCount(len(difficulties))
            for row_idx, entry in enumerate(difficulties):
                items = [
                    _count(row_idx + 1),
                    _japanese(make_table_item(entry.series_name), self._content_style),
                    _count(entry.total_words),
                    _count(entry.unknown_words),
                    # Sorted on the share itself; "9.0%" would rank above "15.0%".
                    make_table_item(
                        f"{entry.difficulty_score * 100:.1f}%",
                        CellRole.NUMBER,
                        sort_value=entry.difficulty_score,
                    ),
                ]
                for col_idx, item in enumerate(items):
                    self.difficulty_table.setItem(row_idx, col_idx, item)
        finally:
            self.difficulty_table.setSortingEnabled(was_sorting)
            self.difficulty_table.setUpdatesEnabled(True)
        hold_numeric_columns(self.difficulty_table, _DIFFICULTY_COUNT_COLUMNS)

    def _update_milestones(self, milestones: list[Milestone]) -> None:
        # Clear existing milestone widgets
        while self.milestones_layout.count():
            child = self.milestones_layout.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()  # type: ignore[union-attr]

        for milestone in milestones:
            self.milestones_layout.addWidget(self._create_milestone_widget(milestone))

    # === ST-05/ST-07: known words and frequency coverage ===

    def _maybe_refresh_knowledge(self) -> None:
        """Start the off-thread Anki scan when the page is on screen and the last one is old."""
        if self._config_provider is None or self._knowledge_in_flight or self._teardown_generation:
            return
        if not self._has_history or not self.isVisible():
            return
        if self._knowledge_at is not None and time.monotonic() - self._knowledge_at < self._KNOWLEDGE_TTL_SECONDS:
            return
        config = self._config_provider()
        service = self.stats_service
        self._knowledge_in_flight = True
        run_off_thread(
            self,
            lambda: _fetch_knowledge(config, service),
            self._on_knowledge_done,
            self._on_knowledge_error,
        )

    def _on_knowledge_done(self, bundle: object) -> None:
        """GUI thread: render the scan; only a scan that reached Anki starts the TTL."""
        self._knowledge_in_flight = False
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            if not isinstance(bundle, _KnowledgeBundle):  # defensive; never expected
                return
            # A scan started before a language switch counts the old language:
            # drop it and count the active one (Review Focus 5).
            if self._config_provider is not None and bundle.language != config_language(self._config_provider()):
                self._maybe_refresh_knowledge()
                return
            # A landed scan clears its own failure, never a refresh's (D24).
            banner = self.issue_banner()
            issue = banner.current_issue() if banner is not None else None
            if issue is not None and issue.action_id == "analytics.knowledge-retry":
                self.clear_screen_issue()
            if bundle.known_count is not None:
                self._knowledge_at = time.monotonic()
            self._apply_knowledge(bundle)

    def _on_knowledge_error(self, msg: str) -> None:
        """GUI thread: report on screen, with Retry (D24)."""
        self._knowledge_in_flight = False
        logging.getLogger(__name__).warning("Analytics: counting known words failed: %s", msg)
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Known words could not be counted."),
                    details=msg,
                    action_id="analytics.knowledge-retry",
                    action_text=self.tr("Retry"),
                ),
                action=self._maybe_refresh_knowledge,
            )

    def _apply_knowledge(self, bundle: _KnowledgeBundle) -> None:
        """Render one scan: the Known Words card (and, from L2.ST07.T3, coverage)."""
        self._render_known_words(
            bundle.language, bundle.known_count, bundle.history, anki_down=bundle.known_count is None
        )
        self._render_coverage(bundle)

    def _render_known_words(
        self, language: str, count: int | None, history: list[KnownSizePoint], *, anki_down: bool
    ) -> None:
        """Show the live count, else the last recorded one; hide the card when there is neither."""
        latest = count if count is not None else (history[-1].word_count if history else None)
        self.card_known_words.setVisible(latest is not None)
        self.known_words_caption.setVisible(latest is not None)
        if latest is None:
            return
        self.card_known_words.set_value(f"{latest:,}")
        parts = [tr_format(self.tr("Words Anki Miner treats as known in %1."), language_display_name(language))]
        reference = _growth_reference(history, today=date.today())
        if reference is not None:
            change = history[-1].word_count - reference.word_count
            parts.append(tr_format(self.tr("Change since %1: %2"), reference.day.isoformat(), f"{change:+,}"))
        if count is None and history:
            last_day = history[-1].day.isoformat()
            if anki_down:
                parts.append(tr_format(self.tr("Last counted on %1; Anki was not reachable."), last_day))
            else:
                parts.append(tr_format(self.tr("Last counted on %1."), last_day))
        self.known_words_caption.setText(" ".join(parts))

    def _render_coverage(self, bundle: _KnowledgeBundle) -> None:
        """Fill the band table, or say why there is none; hidden without a numeric list."""
        self._coverage_available = bundle.has_numeric_source
        self._coverage_card.setVisible(bundle.has_numeric_source and self._has_history)
        self._coverage = bundle.coverage
        if not bundle.has_numeric_source:
            return
        if bundle.coverage is None:
            self.coverage_status_label.setText(
                self.tr("Coverage needs Anki running: it compares the list with your cards.")
            )
            self.coverage_status_label.show()
            self.coverage_source_label.hide()
            self.coverage_table.hide()
            self.show_missing_button.hide()
            return
        self.coverage_status_label.hide()
        self.coverage_source_label.setText(
            tr_format(
                self.tr("How many of the most frequent words in %1 you already know."), bundle.coverage.source_name
            )
        )
        self.coverage_source_label.show()
        table = self.coverage_table
        table.setRowCount(len(bundle.coverage.bands))
        for row_idx, band in enumerate(bundle.coverage.bands):
            share = f"{band.known_share * 100:.1f}%" if band.total else "—"
            items = [
                make_table_item(f"{band.low:,}–{band.high:,}", sort_value=band.low),
                _count(band.known),
                _count(len(band.missing)),
                make_table_item(share, CellRole.NUMBER, sort_value=band.known_share),
            ]
            for col_idx, item in enumerate(items):
                table.setItem(row_idx, col_idx, item)
        hold_numeric_columns(table, _COVERAGE_COUNT_COLUMNS)
        table.show()
        self.show_missing_button.show()
        self._sync_missing_button()

    def _selected_band(self) -> CoverageBand | None:
        """The band whose row is selected, or ``None``."""
        rows = {index.row() for index in self.coverage_table.selectedIndexes()}
        if self._coverage is None or len(rows) != 1:
            return None
        row = rows.pop()
        return self._coverage.bands[row] if 0 <= row < len(self._coverage.bands) else None

    def _sync_missing_button(self) -> None:
        band = self._selected_band()
        self.show_missing_button.setEnabled(band is not None and bool(band.missing))

    def _on_show_missing_clicked(self) -> None:
        """Open the selected band's missing words (non-modal to the page's data)."""
        band = self._selected_band()
        if band is None or self._coverage is None:
            return
        self._missing_dialog = _MissingWordsDialog(self, band, self._coverage.source_name, self._content_style)
        self._missing_dialog.open()

    def _milestone_text(self, milestone: Milestone) -> str:
        """State the milestone as a fact, in the UI language (decision D47).

        The service deliberately ships no wording — rank titles ("Master Miner")
        were minted outside every ``tr()`` seam and reached translated UIs in
        English. The count is grouped the same way the dashboard cards group it.
        """
        count = f"{milestone.threshold:,}"
        if milestone.kind is MilestoneKind.SESSIONS:
            return tr_format(self.tr("%1 mining sessions completed"), count)
        if milestone.kind is MilestoneKind.SERIES:
            return tr_format(self.tr("%1 series mined"), count)
        return tr_format(self.tr("%1 cards created"), count)

    def _create_milestone_widget(self, milestone: Milestone) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(SPACING.xs, SPACING.xxs, SPACING.xs, SPACING.xxs)
        layout.setSpacing(SPACING.sm)

        fact_label = QLabel(self._milestone_text(milestone))
        fact_font = QFont()
        fact_font.setPixelSize(FONT_SIZES.body)
        fact_font.setWeight(QFont.Weight.Bold)
        fact_label.setFont(fact_font)
        layout.addWidget(fact_label, 1)

        # A reached milestone says so; an open one shows how far along it is. The
        # disabled checkbox that used to lead the row looked like a control (E09).
        if milestone.achieved:
            reached = QLabel(self.tr("Reached"))
            reached.setObjectName("row-meta")
            layout.addWidget(reached)
        else:
            progress_bar = QProgressBar()
            progress_bar.setMinimum(0)
            progress_bar.setMaximum(milestone.threshold)
            progress_bar.setValue(min(milestone.current_value, milestone.threshold))
            progress_bar.setFormat(f"{milestone.current_value}/{milestone.threshold}")
            progress_bar.setTextVisible(True)
            progress_bar.setMaximumWidth(150)
            progress_bar.setMinimumWidth(100)
            layout.addWidget(progress_bar)

        widget.setLayout(layout)
        return widget

    def showEvent(self, event) -> None:
        """Refresh data when tab becomes visible."""
        super().showEvent(event)
        self.refresh_data()
        # The refresh above may be skipped by its TTL; the scan has its own.
        self._maybe_refresh_knowledge()

"""Analytics tab for mining statistics, difficulty ranking, and progress tracking."""

import contextlib
import logging
import time
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.fonts import japanese_cell_font
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
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    ScreenIssueHost,
    configure_card_layout,
    configure_scrolled_page,
)
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader, StatCard
from anki_miner.models.stats import (
    DifficultyEntry,
    Milestone,
    MilestoneKind,
    MiningSession,
    OverallStats,
)
from anki_miner.services.stats_service import StatsService
from anki_miner.utils.i18n import tr_format

#: Rows a populated analytics table must show before it reads as "a table".
MIN_VISIBLE_ROWS = 6

#: Recent Sessions: date, series, episode, words, new words, cards.
_SESSION_COUNT_COLUMNS = (3, 4, 5)
#: The date needs one width and keeps it; only the two names should stretch.
_SESSION_FIT_COLUMNS = (0, *_SESSION_COUNT_COLUMNS)

#: Series Difficulty: rank, series, avg words, avg unknown, difficulty share.
_DIFFICULTY_COUNT_COLUMNS = (0, 2, 3, 4)
_DIFFICULTY_FIT_COLUMNS = _DIFFICULTY_COUNT_COLUMNS


def _count(value: int) -> SortableTableWidgetItem:
    """Build a count cell: grouped for reading, sorted on the number itself.

    Grouping matches the dashboard cards above the table, and it is exactly why
    the sort value has to be the integer -- "1,200" sorts before "900" as text.
    """
    return make_table_item(f"{value:,}", CellRole.NUMBER, sort_value=value)


def _japanese(item: SortableTableWidgetItem) -> SortableTableWidgetItem:
    """Give a name cell the Japanese face, and nothing else.

    Series and episode names are usually Japanese, and this app also ships
    Simplified and Traditional Chinese interfaces, which prefer different shapes
    for the same characters (decision D45-B). The cell font carries no size, so
    it resolves against the view's own and the row height does not move.
    """
    item.setFont(japanese_cell_font())
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


class AnalyticsTab(ScreenIssueHost, QWidget):
    """Tab displaying mining analytics, difficulty rankings, and milestones."""

    #: Tables and queue rows genuinely use the extra width.
    PAGE_WIDTH = PageWidth.PAGE

    # showEvent fires on every tab switch. Skip the refresh if data is fresh
    # within this window so rapid tab clicking stays snappy.
    _REFRESH_TTL_SECONDS = 5.0

    def __init__(self, stats_service: StatsService, parent=None):
        super().__init__(parent)
        self.stats_service = stats_service
        self._last_refresh: float | None = None
        # Guard against overlapping off-thread refreshes stacking up (a fast
        # tab switch fires showEvent repeatedly). Cleared in on_done/on_error.
        self._refresh_in_flight: bool = False
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
        self._setup_ui()
        self._setup_accessibility()

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

        # Section 1: Overview Dashboard
        layout.addWidget(self._create_dashboard_section())

        # Section 2: Recent Sessions
        layout.addWidget(self._create_recent_sessions_section())

        # Section 3: Series Difficulty
        layout.addWidget(self._create_difficulty_section())

        # Section 4: Milestones
        layout.addWidget(self._create_milestones_section())

        # Reset (far left, away from Refresh) and Refresh buttons
        button_layout = QHBoxLayout()
        self.reset_button = ModernButton(self.tr("Reset Statistics…"), variant="critical")
        self.reset_button.setToolTip(
            self.tr("Delete every recorded mining session and difficulty score. This cannot be undone.")
        )
        # Armed only once a refresh proves there is something to delete, so an
        # empty database never raises a confirmation with nothing behind it.
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self._on_reset_clicked)
        button_layout.addWidget(self.reset_button)
        button_layout.addStretch()
        self.refresh_button = ModernButton(self.tr("Refresh"), variant="secondary")
        self.refresh_button.clicked.connect(lambda: self.refresh_data(force=True))
        button_layout.addWidget(self.refresh_button)
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

        Text size is applied live (Settings -> UI), so a row height computed once
        at construction goes stale the moment the user changes it -- which is the
        same "pixel constant frozen against a font that later grows" mistake the
        row sizing exists to fix.
        """
        from PyQt6.QtCore import QEvent

        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.FontChange:
            for table in (getattr(self, "sessions_table", None), getattr(self, "difficulty_table", None)):
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
        self.card_avg_cards = StatCard(value="0", label=self.tr("Avg Cards/Session"))

        grid.addWidget(self.card_total_cards, 0, 0)
        grid.addWidget(self.card_total_sessions, 0, 1)
        grid.addWidget(self.card_total_series, 0, 2)
        grid.addWidget(self.card_avg_cards, 0, 3)

        layout.addLayout(grid)
        group.setLayout(layout)
        return group

    def _create_recent_sessions_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        header = SectionHeader(self.tr("Recent Sessions"))
        layout.addWidget(header)

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
            force: Skip the staleness check. The Refresh button passes
                ``force=True``; ``showEvent`` does not.
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
        # Refresh click while a showEvent refresh is still running) would stack
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
                sessions=service.get_recent_sessions(limit=20),
                difficulties=service.get_series_difficulty(),
                milestones=service.get_milestones(stats=stats),
            )

        run_off_thread(
            self,
            _fetch,
            self._on_refresh_done,
            self._on_refresh_error,
        )

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
            self._last_refresh = time.monotonic()

    def _on_refresh_error(self, msg: str) -> None:
        """GUI thread: clear the in-flight flag, log, and say so on screen.

        The whole tab *is* the fetch, so a failure that only reached the log
        left the user reading stale numbers with no way to tell (D24). The
        in-flight flag is cleared first on purpose: the banner offers Retry,
        and Retry is a ``refresh_data`` call the guard would otherwise swallow.
        """
        self._refresh_in_flight = False
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
            self.tr(
                "Delete every recorded mining session and series difficulty score? "
                "This cannot be undone. Your Anki cards, known words, and settings "
                "are not affected."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._reset_in_flight = True
        self.reset_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        service = self.stats_service
        run_off_thread(self, service.reset, self._on_reset_done, self._on_reset_error)

    def _on_reset_done(self, _removed: object) -> None:
        """GUI thread: re-read the now-empty tables so the tab shows the result."""
        self._reset_in_flight = False
        if self._teardown_generation:
            return
        with contextlib.suppress(RuntimeError):
            self.refresh_button.setEnabled(True)
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
            self.refresh_button.setEnabled(True)
            self.reset_button.setEnabled(True)
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr("Statistics could not be reset."),
                    details=msg,
                )
            )

    def _apply_bundle(self, bundle: _AnalyticsBundle) -> None:
        """Render every section from a pre-fetched bundle (GUI thread)."""
        # Nothing recorded means nothing to reset: leave the button disabled
        # rather than raise a confirmation over an empty database.
        self.reset_button.setEnabled(bundle.stats.total_sessions > 0 or bool(bundle.difficulties))
        self._update_dashboard(bundle.stats)
        self._update_recent_sessions(bundle.sessions)
        self._update_difficulty_ranking(bundle.difficulties)
        self._update_milestones(bundle.milestones)

    def _update_dashboard(self, stats) -> None:
        self.card_total_cards.set_value(f"{stats.total_cards_created:,}")
        self.card_total_sessions.set_value(str(stats.total_sessions))
        self.card_total_series.set_value(str(stats.series_count))
        self.card_avg_cards.set_value(f"{stats.avg_cards_per_session:.1f}")

    def _update_recent_sessions(self, sessions: list[MiningSession]) -> None:
        has_sessions = len(sessions) > 0
        self.sessions_table.setVisible(has_sessions)
        self.sessions_empty_label.setVisible(not has_sessions)

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
                    _japanese(make_table_item(session.series_name)),
                    _japanese(make_table_item(session.episode_name)),
                    _count(session.total_words),
                    _count(session.unknown_words),
                    _count(session.cards_created),
                ]
                for col_idx, item in enumerate(items):
                    self.sessions_table.setItem(row_idx, col_idx, item)
        finally:
            self.sessions_table.setSortingEnabled(was_sorting)
            self.sessions_table.setUpdatesEnabled(True)
        hold_numeric_columns(self.sessions_table, _SESSION_COUNT_COLUMNS)

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
                    _japanese(make_table_item(entry.series_name)),
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

        # Status indicator using a disabled checkbox for visual consistency
        status_checkbox = QCheckBox()
        status_checkbox.setChecked(milestone.achieved)
        status_checkbox.setEnabled(False)
        layout.addWidget(status_checkbox)

        fact_label = QLabel(self._milestone_text(milestone))
        fact_font = QFont()
        fact_font.setPixelSize(FONT_SIZES.body)
        fact_font.setWeight(QFont.Weight.Bold)
        fact_label.setFont(fact_font)
        layout.addWidget(fact_label, 1)

        # Progress bar
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

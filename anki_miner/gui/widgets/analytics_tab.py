"""Analytics tab for mining statistics, difficulty ranking, and progress tracking."""

import logging
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.qt_helpers import configure_table_header
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader, StatCard
from anki_miner.services.stats_service import StatsService


class AnalyticsTab(QWidget):
    """Tab displaying mining analytics, difficulty rankings, and milestones."""

    # showEvent fires on every tab switch. Skip the refresh if data is fresh
    # within this window so rapid tab clicking stays snappy.
    _REFRESH_TTL_SECONDS = 5.0

    def __init__(self, stats_service: StatsService, parent=None):
        super().__init__(parent)
        self.stats_service = stats_service
        self._last_refresh: float | None = None
        self._setup_ui()
        self._setup_accessibility()

    def _setup_ui(self) -> None:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

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

        # Refresh button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.refresh_button = ModernButton(self.tr("Refresh"), variant="secondary")
        self.refresh_button.clicked.connect(lambda: self.refresh_data(force=True))
        button_layout.addWidget(self.refresh_button)
        layout.addLayout(button_layout)

        layout.addStretch()

        container.setLayout(layout)
        scroll_area.setWidget(container)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    def _setup_accessibility(self) -> None:
        self.setAccessibleName("Analytics Tab")
        self.setAccessibleDescription("View mining statistics, series difficulty rankings, and progress milestones")

    def _create_dashboard_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

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
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        header = SectionHeader(self.tr("Recent Sessions"))
        layout.addWidget(header)

        self.sessions_empty_label = QLabel(self.tr("No sessions yet. Process an episode to see your history here."))
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
        configure_table_header(self.sessions_table)
        self.sessions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sessions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sessions_table.setMinimumHeight(200)

        v_header = self.sessions_table.verticalHeader()
        if v_header:
            v_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.sessions_table.hide()

        layout.addWidget(self.sessions_table)
        group.setLayout(layout)
        return group

    def _create_difficulty_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        header = SectionHeader(self.tr("Series Difficulty Ranking"))
        layout.addWidget(header)

        explanation = QLabel(
            self.tr(
                "Difficulty is based on the ratio of unknown words. "
                "Lower scores mean easier content for your current level."
            )
        )
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
        configure_table_header(self.difficulty_table)
        self.difficulty_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.difficulty_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.difficulty_table.setMinimumHeight(200)

        v_header = self.difficulty_table.verticalHeader()
        if v_header:
            v_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.difficulty_table.hide()

        layout.addWidget(self.difficulty_table)
        group.setLayout(layout)
        return group

    def _create_milestones_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        header = SectionHeader(self.tr("Milestones"))
        layout.addWidget(header)

        self.milestones_layout = QVBoxLayout()
        self.milestones_layout.setSpacing(SPACING.xs)
        layout.addLayout(self.milestones_layout)

        group.setLayout(layout)
        return group

    def refresh_data(self, force: bool = False) -> None:
        """Refresh all analytics data from the stats service.

        Args:
            force: Skip the staleness check. The Refresh button passes
                ``force=True``; ``showEvent`` does not.
        """
        if not self.stats_service.is_available():
            return

        if (
            not force
            and self._last_refresh is not None
            and time.monotonic() - self._last_refresh < self._REFRESH_TTL_SECONDS
        ):
            return

        try:
            stats = self.stats_service.get_overall_stats()
            self._update_dashboard(stats)
            self._update_recent_sessions()
            self._update_difficulty_ranking()
            self._update_milestones(stats)
            self._last_refresh = time.monotonic()
        except Exception:
            logging.getLogger(__name__).exception("Failed to refresh analytics data")

    def _update_dashboard(self, stats) -> None:
        self.card_total_cards.set_value(f"{stats.total_cards_created:,}")
        self.card_total_sessions.set_value(str(stats.total_sessions))
        self.card_total_series.set_value(str(stats.series_count))
        self.card_avg_cards.set_value(f"{stats.avg_cards_per_session:.1f}")

    def _update_recent_sessions(self) -> None:
        sessions = self.stats_service.get_recent_sessions(limit=20)
        has_sessions = len(sessions) > 0
        self.sessions_table.setVisible(has_sessions)
        self.sessions_empty_label.setVisible(not has_sessions)

        self.sessions_table.setUpdatesEnabled(False)
        was_sorting = self.sessions_table.isSortingEnabled()
        self.sessions_table.setSortingEnabled(False)
        try:
            self.sessions_table.setRowCount(len(sessions))
            for row_idx, session in enumerate(sessions):
                date_str = session.mined_at.strftime("%Y-%m-%d %H:%M")
                items = [
                    QTableWidgetItem(date_str),
                    QTableWidgetItem(session.series_name),
                    QTableWidgetItem(session.episode_name),
                    QTableWidgetItem(str(session.total_words)),
                    QTableWidgetItem(str(session.unknown_words)),
                    QTableWidgetItem(str(session.cards_created)),
                ]
                for col_idx, item in enumerate(items):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip(item.text())
                    self.sessions_table.setItem(row_idx, col_idx, item)
        finally:
            self.sessions_table.setSortingEnabled(was_sorting)
            self.sessions_table.setUpdatesEnabled(True)

    def _update_difficulty_ranking(self) -> None:
        difficulties = self.stats_service.get_series_difficulty()
        has_difficulties = len(difficulties) > 0
        self.difficulty_table.setVisible(has_difficulties)
        self.difficulty_empty_label.setVisible(not has_difficulties)

        self.difficulty_table.setUpdatesEnabled(False)
        was_sorting = self.difficulty_table.isSortingEnabled()
        self.difficulty_table.setSortingEnabled(False)
        try:
            self.difficulty_table.setRowCount(len(difficulties))
            for row_idx, entry in enumerate(difficulties):
                difficulty_pct = f"{entry.difficulty_score * 100:.1f}%"
                items = [
                    QTableWidgetItem(str(row_idx + 1)),
                    QTableWidgetItem(entry.series_name),
                    QTableWidgetItem(str(entry.total_words)),
                    QTableWidgetItem(str(entry.unknown_words)),
                    QTableWidgetItem(difficulty_pct),
                ]
                for col_idx, item in enumerate(items):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip(item.text())
                    self.difficulty_table.setItem(row_idx, col_idx, item)
        finally:
            self.difficulty_table.setSortingEnabled(was_sorting)
            self.difficulty_table.setUpdatesEnabled(True)

    def _update_milestones(self, stats) -> None:
        # Clear existing milestone widgets
        while self.milestones_layout.count():
            child = self.milestones_layout.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()  # type: ignore[union-attr]

        milestones = self.stats_service.get_milestones(stats=stats)
        for milestone in milestones:
            self.milestones_layout.addWidget(self._create_milestone_widget(milestone))

    def _create_milestone_widget(self, milestone) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(SPACING.xs, SPACING.xxs, SPACING.xs, SPACING.xxs)
        layout.setSpacing(SPACING.sm)

        # Status indicator using a disabled checkbox for visual consistency
        status_checkbox = QCheckBox()
        status_checkbox.setChecked(milestone.achieved)
        status_checkbox.setEnabled(False)
        layout.addWidget(status_checkbox)

        # Name and description
        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        name_label = QLabel(milestone.name)
        name_font = QFont()
        name_font.setPixelSize(FONT_SIZES.body)
        name_font.setWeight(QFont.Weight.Bold)
        name_label.setFont(name_font)
        info_layout.addWidget(name_label)

        desc_label = QLabel(milestone.description)
        desc_font = QFont()
        desc_font.setPixelSize(FONT_SIZES.caption)
        desc_label.setFont(desc_font)
        info_layout.addWidget(desc_label)

        layout.addLayout(info_layout, 1)

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

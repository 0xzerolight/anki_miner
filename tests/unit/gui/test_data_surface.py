"""The shared data surface (decision D42) as the real screens wear it.

``test_qt_helpers.py`` pins the contract; this file pins the three surfaces that
consume it -- Analytics, the word curator, and Known Words -- because a contract
nothing is configured through is just a module. The curator and Known Words are
the densest Japanese views in the app, so their geometry is asserted to come
from rendered metrics rather than from a constant that stops tracking the text
scale.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView

from anki_miner.gui.utils.qt_helpers import SORT_ROLE, data_row_height
from anki_miner.models.stats import DifficultyEntry, MiningSession, OverallStats

_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


@pytest.fixture(autouse=True)
def _no_app_stylesheet(qapp):
    """Row heights here are asserted against widget fonts, and a leaked QSS
    ``font-size`` overrides ``setFont`` -- which would void every such check."""
    previous = qapp.styleSheet()
    qapp.setStyleSheet("")
    yield
    qapp.setStyleSheet(previous)


def _session(name: str, words: int, unknown: int, cards: int, mined_at: datetime) -> MiningSession:
    return MiningSession(
        id=1,
        series_name="Shirobako",
        episode_name=name,
        total_words=words,
        unknown_words=unknown,
        cards_created=cards,
        mined_at=mined_at,
    )


def _render(tab, sessions, difficulties=()) -> None:
    """Render a bundle straight onto the GUI thread.

    ``refresh_data`` fetches through a worker; driving the render directly keeps
    these tests about the rendered surface, with no thread to join per test.
    """
    from anki_miner.gui.widgets.analytics_tab import _AnalyticsBundle

    sessions = list(sessions)
    tab._apply_bundle(
        _AnalyticsBundle(
            stats=OverallStats(
                total_sessions=len(sessions),
                total_cards_created=sum(s.cards_created for s in sessions),
                total_words_encountered=sum(s.total_words for s in sessions),
                total_unknown_words=sum(s.unknown_words for s in sessions),
                series_count=1,
            ),
            sessions=sessions,
            difficulties=list(difficulties),
            milestones=[],
        )
    )


def _analytics(qtbot, sessions, difficulties=()):
    from anki_miner.gui.widgets.analytics_tab import AnalyticsTab

    service = MagicMock()
    service.is_available.return_value = False  # showEvent must not start a worker
    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    _render(tab, sessions, difficulties)
    return tab


_SESSIONS = [
    _session("Episode 1", 900, 90, 9, datetime(2026, 3, 1, 9, 0)),
    _session("Episode 2", 1200, 120, 100, datetime(2026, 1, 12, 9, 0)),
]
_DIFFICULTIES = [
    DifficultyEntry(series_name="Shirobako", total_words=900, unknown_words=90, difficulty_score=0.09),
    DifficultyEntry(series_name="Monogatari", total_words=1200, unknown_words=180, difficulty_score=0.15),
]


class TestAnalyticsReadsAsData:
    def test_text_columns_are_left_aligned(self, qtbot):
        table = _analytics(qtbot, _SESSIONS).sessions_table

        for column in (0, 1, 2):  # date, series, episode
            assert table.item(0, column).textAlignment() == _LEFT, f"column {column}"

    def test_count_columns_are_right_aligned(self, qtbot):
        table = _analytics(qtbot, _SESSIONS).sessions_table

        for column in (3, 4, 5):  # words, new words, cards
            assert table.item(0, column).textAlignment() == _RIGHT, f"column {column}"

    def test_there_is_no_row_number_column(self, qtbot):
        tab = _analytics(qtbot, _SESSIONS, _DIFFICULTIES)

        for table in (tab.sessions_table, tab.difficulty_table):
            assert table.verticalHeader().isHidden()

    def test_the_grid_is_gone(self, qtbot):
        tab = _analytics(qtbot, _SESSIONS, _DIFFICULTIES)

        assert tab.sessions_table.showGrid() is False
        assert tab.difficulty_table.showGrid() is False

    def test_columns_are_not_all_stretched_equally(self, qtbot):
        """A three-digit card count does not need an episode title's width."""
        header = _analytics(qtbot, _SESSIONS).sessions_table.horizontalHeader()

        assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch  # episode
        assert header.sectionResizeMode(5) != QHeaderView.ResizeMode.Stretch  # cards

    def test_counts_sort_by_their_value_not_their_printed_string(self, qtbot):
        """ "1,200" prints before "900" but is the larger number."""
        table = _analytics(qtbot, _SESSIONS).sessions_table

        table.sortItems(3, Qt.SortOrder.AscendingOrder)

        assert [table.item(row, 3).data(SORT_ROLE) for row in range(2)] == [900, 1200]

    def test_dates_sort_by_their_instant(self, qtbot):
        table = _analytics(qtbot, _SESSIONS).sessions_table

        table.sortItems(0, Qt.SortOrder.AscendingOrder)

        assert table.item(0, 0).text().startswith("2026-01-12")

    def test_the_difficulty_share_sorts_by_its_number(self, qtbot):
        table = _analytics(qtbot, _SESSIONS, _DIFFICULTIES).difficulty_table

        table.sortItems(4, Qt.SortOrder.DescendingOrder)

        assert table.item(0, 4).text() == "15.0%"

    def test_a_count_column_does_not_shrink_between_refreshes(self, qtbot):
        tab = _analytics(qtbot, _SESSIONS)
        wide = tab.sessions_table.columnWidth(3)

        _render(tab, [_SESSIONS[0]])

        assert tab.sessions_table.rowCount() == 1
        assert tab.sessions_table.columnWidth(3) == wide

    def test_row_height_comes_from_the_shared_metric(self, qtbot):
        """Asserted after a populate: sorting is toggled around every render, and
        the row height has to survive it."""
        table = _analytics(qtbot, _SESSIONS).sessions_table

        header = table.verticalHeader()
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        assert header.defaultSectionSize() == data_row_height(table)

    def test_a_row_copies_as_tab_separated_values(self, qtbot, qapp):
        table = _analytics(qtbot, _SESSIONS).sessions_table
        table.selectRow(0)

        _trigger_copy(table)

        assert qapp.clipboard().text().startswith("2026-03-01")
        assert "Episode 1" in qapp.clipboard().text()


def _trigger_copy(view) -> None:
    """Fire the view's installed copy shortcut without a real key event."""
    from PyQt6.QtGui import QShortcut

    shortcuts = [s for s in view.findChildren(QShortcut) if s.key().toString() == "Ctrl+C"]
    assert shortcuts, "no copy shortcut installed on this view"
    shortcuts[0].activated.emit()


class TestCuratorGeometryComesFromMetrics:
    def test_row_height_is_the_shared_metric(self, qtbot, make_tokenized_words):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dialog = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dialog)

        header = dialog.table.verticalHeader()
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        assert header.defaultSectionSize() == data_row_height(dialog.table)

    def test_row_height_grows_with_the_font(self, qtbot, make_tokenized_words, font_scale):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        font_scale(1.0)
        small = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(small)
        baseline = small.table.verticalHeader().defaultSectionSize()

        font_scale(1.5)
        large = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(large)

        assert large.table.verticalHeader().defaultSectionSize() > baseline

    def test_the_checkbox_column_fits_its_indicator(self, qtbot, make_tokenized_words):
        from PyQt6.QtWidgets import QStyle

        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dialog = WordCurationDialog(make_tokenized_words(1))
        qtbot.addWidget(dialog)

        indicator = dialog.table.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth)
        assert dialog.table.columnWidth(0) > indicator

    def test_japanese_columns_are_left_aligned_and_counts_right(self, qtbot, make_tokenized_word):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        word = make_tokenized_word(
            surface="食べた",
            lemma="食べる",
            reading="タベル",
            sentence="毎日ご飯を食べた。",
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            frequency_rank=1200,
        )
        dialog = WordCurationDialog([word])
        qtbot.addWidget(dialog)

        for column in (1, 2, 3, 4):  # mined form, surface, reading, sentence
            assert dialog.table.item(0, column).textAlignment() == _LEFT, f"column {column}"
        for column in (5, 6):  # frequency rank, occurrences
            assert dialog.table.item(0, column).textAlignment() == _RIGHT, f"column {column}"

    def test_a_copied_row_carries_the_whole_sentence(self, qtbot, qapp, make_tokenized_word):
        """The sentence cell is truncated for display; the copy is not."""
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        sentence = "毎日" * 40
        word = make_tokenized_word(
            surface="食べた",
            lemma="食べる",
            reading="タベル",
            sentence=sentence,
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
        )
        dialog = WordCurationDialog([word])
        qtbot.addWidget(dialog)
        dialog.table.selectRow(0)

        _trigger_copy(dialog.table)

        assert sentence in qapp.clipboard().text()

    def test_the_original_index_mapping_survives(self, qtbot, make_tokenized_words):
        """Column 0's UserRole is how a visual row finds its word."""
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dialog = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dialog)

        indices = {dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(3)}
        assert indices == {0, 1, 2}


class TestKnownWordsIsADataSurface:
    @staticmethod
    def _dialog(qtbot, tmp_path, words):
        from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog
        from anki_miner.services.known_word_db import KnownWordDB

        db = KnownWordDB(tmp_path / "known.db")
        db.initialize()
        db.add_words(words, source="user")
        dialog = KnownWordsManagerDialog(db)
        qtbot.addWidget(dialog)
        return dialog

    def test_rows_use_the_shared_metric(self, qtbot, tmp_path):
        dialog = self._dialog(qtbot, tmp_path, {"食べる", "走る"})

        assert dialog.word_list.sizeHintForRow(0) >= data_row_height(dialog.word_list)

    def test_selected_words_copy_one_per_line(self, qtbot, qapp, tmp_path):
        dialog = self._dialog(qtbot, tmp_path, {"食べる", "走る"})
        for row in range(dialog.word_list.count()):
            dialog.word_list.item(row).setSelected(True)

        _trigger_copy(dialog.word_list)

        assert set(qapp.clipboard().text().split("\n")) == {"食べる", "走る"}

    def test_the_list_scrolls_per_pixel(self, qtbot, tmp_path):
        from PyQt6.QtWidgets import QAbstractItemView

        dialog = self._dialog(qtbot, tmp_path, {"食べる"})

        assert dialog.word_list.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel


class TestRowsSurviveEveryTextScale:
    """0.8x, 1.0x and 1.5x: rows track the font and never crush their content."""

    @pytest.mark.parametrize("scale", (0.8, 1.0, 1.5))
    def test_analytics_rows_clear_their_own_text(self, qtbot, font_scale, scale):
        font_scale(scale)
        table = _analytics(qtbot, _SESSIONS).sessions_table

        assert table.verticalHeader().defaultSectionSize() >= table.fontMetrics().height()

    @pytest.mark.parametrize("scale", (0.8, 1.0, 1.5))
    def test_known_words_rows_clear_their_own_text(self, qtbot, font_scale, tmp_path, scale):
        font_scale(scale)
        dialog = TestKnownWordsIsADataSurface._dialog(qtbot, tmp_path, {"営業部の会議"})

        assert dialog.word_list.sizeHintForRow(0) >= dialog.word_list.fontMetrics().height()

"""One tightened density for the whole app (D40) — shorter, never smaller.

The owner asked for more of the app on screen without losing a single control
and without shrinking a single font: rows, controls, cards and field gaps give
up their slack, the type does not.

Every oracle here is written as *one line of text plus the padding that is
actually declared*, never as a pixel literal. A literal would pin today's font
and start lying at 0.8x or 1.5x text, which is the exact failure the removed
``min-height`` floors already produced: a 28px floor is generous at 100% and
below the text at 150%, so it crushed nothing on the developer's machine and
clipped on the user's.

The theme is applied through the ``font_scale`` fixture rather than left to
whatever an earlier module installed: the padding under test lives in
``common.qss``, so a measurement taken with no application stylesheet would be
measuring Qt's defaults instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLineEdit,
    QTableWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import CellRole, configure_data_view, data_row_height, make_table_item
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

#: The 1px box every control carries in ``common.qss``, top and bottom.
BORDER = 2

#: Qt's own styles add a fixed pixel or two of their own on top of the box the
#: stylesheet declares (measured on this runtime: 1 for QPushButton, 2 for
#: QLineEdit's editing margin). It is not slack anyone can spend, so it is
#: allowed for by name rather than absorbed into a fudged padding value. The
#: assertions stay falsifiable: the 28px floor these tests removed put a button
#: 6px past even this allowance.
QT_OWN_FRAME = 2


def _one_line_box(widget) -> int:
    """The height one line of this widget's text needs inside the tight box."""
    widget.ensurePolished()
    return widget.fontMetrics().height() + 2 * SPACING.xxs + BORDER + QT_OWN_FRAME


class TestControlsAreOneLineOfTextTall:
    """Buttons and inputs used to carry a 28px QSS floor plus a 36px Python one.

    Neither tracked the font, so both were slack at 100% and irrelevant at 150%.
    """

    def test_a_button_is_a_line_of_text_plus_its_padding(self, qtbot, font_scale):
        font_scale(1.0)
        button = ModernButton("Process Episode")
        qtbot.addWidget(button)

        assert button.sizeHint().height() <= _one_line_box(button)

    def test_a_line_edit_is_a_line_of_text_plus_its_padding(self, qtbot, font_scale):
        font_scale(1.0)
        field = QLineEdit()
        qtbot.addWidget(field)

        assert field.sizeHint().height() <= _one_line_box(field)

    def test_a_combo_box_is_a_line_of_text_plus_its_padding(self, qtbot, font_scale):
        font_scale(1.0)
        combo = QComboBox()
        qtbot.addWidget(combo)

        assert combo.sizeHint().height() <= _one_line_box(combo)

    @pytest.mark.parametrize("scale", [0.8, 1.5])
    def test_no_control_is_ever_shorter_than_its_own_text(self, qtbot, font_scale, scale):
        """Tighter padding must not turn into clipping at either extreme."""
        font_scale(scale)
        button = ModernButton("Process Episode")
        field = QLineEdit()
        combo = QComboBox()
        for widget in (button, field, combo):
            qtbot.addWidget(widget)
            widget.ensurePolished()
            assert widget.sizeHint().height() >= widget.fontMetrics().height()

    def test_the_button_floor_tracks_the_text_scale(self, qtbot, font_scale):
        """``setMinimumHeight(36)`` was the same 36 at 80% text as at 150%."""
        font_scale(0.8)
        small = ModernButton("Process Episode")
        qtbot.addWidget(small)
        small.ensurePolished()
        at_80 = small.minimumHeight()

        font_scale(1.5)
        large = ModernButton("Process Episode")
        qtbot.addWidget(large)
        large.ensurePolished()

        assert large.minimumHeight() > at_80


class TestDataRowsGiveUpTheirSlack:
    """Table/list/tree rows were a line of text inside 8px of padding per edge."""

    def test_a_row_is_a_line_of_text_plus_the_smallest_step(self, qtbot, font_scale):
        font_scale(1.0)
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)
        table.ensurePolished()

        assert data_row_height(table) == table.fontMetrics().lineSpacing() + 2 * SPACING.xxs

    @pytest.mark.parametrize("scale", [1.0, 1.5])
    def test_the_stylesheet_asks_for_no_more_than_the_row_gives(self, qtbot, font_scale, scale):
        """QSS cell padding and the row height are one decision, not two.

        When they disagree the taller one wins and the shorter one clips. The
        1px allowance is Qt's own per-item margin, the same constant the old
        pair also missed by (47px asked inside a 46px row at 150%).
        """
        font_scale(scale)
        table = QTableWidget(3, 2)
        qtbot.addWidget(table)
        configure_data_view(table)
        for row in range(3):
            for column in range(2):
                table.setItem(row, column, make_table_item(f"cell {row}-{column}", CellRole.TEXT))
        table.show()
        qtbot.waitExposed(table)

        assert table.sizeHintForRow(0) <= table.rowHeight(0) + 1
        assert table.rowHeight(0) >= table.fontMetrics().height()

    def test_a_short_table_shows_more_rows(self, qtbot, font_scale):
        """The point of the whole change, stated in rows rather than pixels.

        Measured on this runtime: the same 300px table held 7.0 rows before and
        9.1 after.
        """
        font_scale(1.0)
        table = QTableWidget(40, 2)
        qtbot.addWidget(table)
        configure_data_view(table)
        for row in range(40):
            for column in range(2):
                table.setItem(row, column, make_table_item(f"cell {row}-{column}", CellRole.TEXT))
        table.resize(400, 300)
        table.show()
        qtbot.waitExposed(table)
        QApplication.processEvents()

        visible = table.viewport().height() / table.rowHeight(0)
        assert visible >= 9, f"only {visible:.1f} rows in a 300px table"


class TestFileSelectorSpeaksOnlyWhenItHasSomethingToSay:
    """The helper row under all 31 pickers announced "No file selected" for a
    field the user had simply not filled in yet — the clearest "unfinished"
    signal in the audit's screenshots, and 31 rows of height spent saying it.
    """

    def test_a_blank_picker_shows_no_helper_row(self, qtbot):
        selector = FileSelector(label="Video file:")
        qtbot.addWidget(selector)

        assert selector.status_label.isHidden()

    def test_a_valid_path_shows_no_helper_row(self, qtbot, tmp_path):
        existing = tmp_path / "episode.mkv"
        existing.write_text("", encoding="utf-8")
        selector = FileSelector(label="Video file:")
        qtbot.addWidget(selector)

        selector.set_path(str(existing))

        assert selector.status_label.isHidden()

    def test_a_missing_required_path_says_what_to_do(self, qtbot, tmp_path):
        selector = FileSelector(label="Video file:")
        qtbot.addWidget(selector)

        selector.set_path(str(tmp_path / "gone.mkv"))

        assert not selector.status_label.isHidden()
        assert selector.status_label.text() == "File not found. Choose an existing file."

    def test_a_missing_folder_says_what_to_do(self, qtbot, tmp_path):
        selector = FileSelector(label="Media folder:", file_mode=False)
        qtbot.addWidget(selector)

        selector.set_path(str(tmp_path / "gone"))

        assert not selector.status_label.isHidden()
        assert selector.status_label.text() == "Folder not found. Choose an existing folder."

    def test_a_missing_optional_resource_still_reports_itself(self, qtbot, tmp_path):
        selector = FileSelector(label="Pitch accents:", optional=True)
        qtbot.addWidget(selector)

        selector.set_path(str(tmp_path / "pitch_accent.csv"))

        assert not selector.status_label.isHidden()
        assert selector.status_label.text() == "Not installed"

    def test_the_helper_row_costs_nothing_until_it_appears(self, qtbot, font_scale):
        """Hidden is not the same as blank: a blank row still takes its height."""
        font_scale(1.0)
        selector = FileSelector(label="Video file:")
        qtbot.addWidget(selector)
        selector.ensurePolished()

        quiet = selector.minimumSizeHint().height()
        selector.set_path("/definitely/not/here.mkv")

        assert quiet <= selector.input.minimumSizeHint().height()
        assert selector.minimumSizeHint().height() > quiet

    def test_the_path_is_still_named_for_diagnostics(self, qtbot, tmp_path):
        """Hidden, not emptied: the text stays readable to tests and tooling."""
        existing = tmp_path / "episode.mkv"
        existing.write_text("", encoding="utf-8")
        selector = FileSelector(label="Video file:")
        qtbot.addWidget(selector)

        selector.set_path(str(existing))

        assert selector.status_label.text() == "episode.mkv"


class TestCardsShareOneDensity:
    """Fourteen screens hand-built the same card and each set its own margins."""

    PAGES = (
        "SingleEpisodeTab",
        "BatchProcessingTab",
        "YouTubeTab",
        "AudiobookTab",
        "ReadingMangaTab",
        "ReadingNovelsTab",
        "ReadingSubtitlesTab",
        "ReadingTextTab",
        "SubtitleCreationTab",
        "SubtitleRetimeTab",
        "CondenseTab",
        "DownloadTab",
        "AnalyticsTab",
        "SettingsTab",
    )

    @staticmethod
    def _cards(page) -> list[QFrame]:
        return [card for card in page.findChildren(QFrame, "card") if card.layout() is not None]

    @pytest.mark.parametrize("name", PAGES)
    def test_every_card_uses_the_shared_padding(self, qtbot, test_config, quiet_show, name):
        from tests.unit.gui.test_page_width import _build_page

        page = _build_page(name, test_config)
        qtbot.addWidget(page)

        cards = self._cards(page)
        assert cards, f"{name}: no cards to measure"
        for card in cards:
            layout = card.layout()
            margins = layout.contentsMargins()
            assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
                SPACING.sm,
                SPACING.sm,
                SPACING.sm,
                SPACING.sm,
            ), f"{name}: a card still sets its own margins"
            assert layout.spacing() == SPACING.xs, f"{name}: a card still sets its own gap"

    def test_settings_fields_sit_closer_than_cards_do(self, qtbot):
        """Rows inside one card are more related than the cards are to each
        other, so their gap is the smaller one."""
        from anki_miner.gui.widgets.base.form_panel import FormPanel

        panel = FormPanel("Anki")
        qtbot.addWidget(panel)
        panel.add_field("Deck", QLineEdit())

        assert panel._form_layout.spacing() == SPACING.xxs
        assert panel.main_layout.spacing() == SPACING.xs


@pytest.fixture
def quiet_show(monkeypatch):
    """Silence the pages that fetch from Anki the first time they are shown."""
    from PyQt6.QtWidgets import QWidget

    from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    monkeypatch.setattr(SettingsTab, "showEvent", lambda self, event: QWidget.showEvent(self, event))
    monkeypatch.setattr(AnalyticsTab, "refresh_data", lambda self, *a, **k: None)


def test_the_mining_form_lost_height_without_losing_a_control(qtbot, test_config, font_scale):
    """The receipt for the whole task, on the app's flagship screen.

    The yardstick is the app's own ``WINDOW_MIN_HEIGHT``: this page used to
    demand 866px of column before the window chrome was even drawn — more than
    the whole minimum window — and now demands 734. That is not the same claim as
    "fits without scrolling" at 800px: the Activity console's own 200px floor and
    the two section headings still push the column past the viewport once chrome
    is subtracted, and both belong to other tasks.

    The control inventory is asserted alongside the height, because shorter is
    only the goal while nothing went missing.
    """
    from anki_miner.gui.constants import WINDOW_MIN_HEIGHT
    from anki_miner.gui.widgets.base.sizing import PAGE_SCROLL_OBJECT_NAME
    from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

    font_scale(1.0)
    tab = SingleEpisodeTab(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)
    tab.resize(1024, 700)
    tab.show()
    qtbot.waitExposed(tab)
    QApplication.processEvents()

    from PyQt6.QtWidgets import QScrollArea

    shells = tab.findChildren(QScrollArea, PAGE_SCROLL_OBJECT_NAME)
    assert shells, "the mining page has no page shell to measure"
    column = shells[0].widget()

    assert column.minimumSizeHint().height() < WINDOW_MIN_HEIGHT
    assert tab.video_selector.isVisible() and tab.subtitle_selector.isVisible()
    assert tab.process_button.isVisible() and tab.timing_button.isVisible() and tab.tracks_button.isVisible()
    tab.hide()

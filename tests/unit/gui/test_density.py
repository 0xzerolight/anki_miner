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

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QTableWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import CellRole, configure_data_view, data_row_height, make_table_item
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

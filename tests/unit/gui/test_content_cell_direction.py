"""RTL content cells: the mining profile's direction reaches table and list cells.

S21 flipped three content widgets and left table/list cells as left-to-right
paragraphs (IMPLEMENTATION_STATUS: "an Arabic sentence's final punctuation shows
at the wrong end there"). Arabic literals are \\N{} escapes on purpose.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget, QStyledItemDelegate, QTableWidget

from anki_miner.gui.utils.content_text import ContentCellDelegate, apply_content_direction
from anki_miner.gui.utils.qt_helpers import make_table_item
from anki_miner.languages.registry import get_profile
from tests.unit._cell_paint import ink_span, paint_cell

AR = get_profile("ar").content_style
JA = get_profile("ja").content_style

HADHA = "\N{ARABIC LETTER HEH}\N{ARABIC LETTER THAL}\N{ARABIC LETTER ALEF}"
KITAB = "\N{ARABIC LETTER KAF}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"
AR_SENTENCE = f"{HADHA} {KITAB}"


def _table(qtbot, *texts: str) -> QTableWidget:
    table = QTableWidget(len(texts), 1)
    qtbot.addWidget(table)
    for row, text in enumerate(texts):
        table.setItem(row, 0, make_table_item(text))
    return table


def _spans(table: QTableWidget, delegate) -> list[tuple[int, int]]:
    model = table.model()
    return [ink_span(paint_cell(delegate, table, model.index(row, 0))) for row in range(table.rowCount())]


def test_an_rtl_cell_ends_its_sentence_at_the_logical_end(qtbot):
    table = _table(qtbot, AR_SENTENCE, AR_SENTENCE + ".")
    bare, stopped = _spans(table, ContentCellDelegate(table, lambda: AR))
    assert stopped[1] == bare[1], "the leading (right) edge moved"
    assert stopped[0] < bare[0], "the period is not at the left"


def test_the_stock_delegate_put_the_period_at_the_wrong_end(qtbot):
    """The control: the same cells as left-to-right paragraphs (the v1 state)."""
    table = _table(qtbot, AR_SENTENCE, AR_SENTENCE + ".")
    bare, stopped = _spans(table, QStyledItemDelegate())
    assert stopped[0] == bare[0]
    assert stopped[1] > bare[1]


@pytest.mark.parametrize("text", ["これは本です。", AR_SENTENCE + "."], ids=["ja-text", "ar-text"])
def test_an_ltr_style_paints_byte_identically_to_the_stock_delegate(qtbot, text):
    table = _table(qtbot, text)
    index = table.model().index(0, 0)
    ours = paint_cell(ContentCellDelegate(table, lambda: JA), table, index)
    assert ours == paint_cell(QStyledItemDelegate(), table, index)


def test_a_long_rtl_sentence_keeps_its_start_in_view(qtbot):
    """Elision takes the logical end (the left): the first word stays at the right edge."""
    table = _table(qtbot, " ".join([AR_SENTENCE] * 30), HADHA)
    assert table.textElideMode() == Qt.TextElideMode.ElideRight
    delegate = ContentCellDelegate(table, lambda: AR)
    model = table.model()
    elided = paint_cell(delegate, table, model.index(0, 0), size=(200, 32))
    first_word = paint_cell(delegate, table, model.index(1, 0), size=(200, 32))
    left, right = ink_span(first_word)
    width = right - left + 1
    assert elided.copy(left, 0, width, 32) == first_word.copy(left, 0, width, 32)


def test_the_style_is_read_at_paint_time(qtbot):
    """A screen whose mining language switches in-session needs no re-install."""
    current = [JA]
    table = _table(qtbot, AR_SENTENCE + ".")
    delegate = ContentCellDelegate(table, lambda: current[0])
    index = table.model().index(0, 0)
    ltr = ink_span(paint_cell(delegate, table, index))
    current[0] = AR
    rtl = ink_span(paint_cell(delegate, table, index))
    assert rtl[0] > ltr[0] and rtl[1] > ltr[1], "the cell did not move to its leading (right) edge"


def test_a_content_list_flips_whole_and_an_ltr_one_is_never_touched(qtbot):
    rtl, ltr = QListWidget(), QListWidget()
    qtbot.addWidget(rtl)
    qtbot.addWidget(ltr)
    apply_content_direction(rtl, AR)
    apply_content_direction(ltr, JA)
    assert rtl.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert not ltr.testAttribute(Qt.WidgetAttribute.WA_SetLayoutDirection)

"""D5: a first-time curator shows fewer columns; the text form rides in the Word column."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from anki_miner.gui.utils import session_state
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs import word_curation_dialog as module
from anki_miner.gui.widgets.dialogs.word_curation_dialog import POSITION_COLUMN, WordCurationDialog
from anki_miner.models import TokenizedWord
from tests.unit._cell_paint import ink_span, paint_cell

_WORD_COL, _FORM_COL, _UNKNOWNS_COL, _LENGTH_COL = 1, 2, 7, 8


@pytest.fixture(autouse=True)
def _isolated_ui_state(tmp_path, monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")


def _verb() -> TokenizedWord:
    return TokenizedWord(
        surface="食べた",
        lemma="食べる",
        reading="タベタ",
        sentence="ご飯を食べた",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="動詞",
        frequency_rank=500,
        line_unknown_count=1,
    )


def test_a_first_time_curator_hides_three_columns(qtbot):
    dialog = WordCurationDialog([_verb()])
    qtbot.addWidget(dialog)

    assert dialog.table.isColumnHidden(_FORM_COL)
    assert dialog.table.isColumnHidden(_UNKNOWNS_COL)
    assert dialog.table.isColumnHidden(_LENGTH_COL)
    assert not dialog.table.isColumnHidden(POSITION_COLUMN)


def test_a_saved_arrangement_wins(qtbot):
    first = WordCurationDialog([_verb()])
    qtbot.addWidget(first)
    first.table.setColumnHidden(_FORM_COL, False)
    first.reject()

    second = WordCurationDialog([_verb()])
    qtbot.addWidget(second)

    assert not second.table.isColumnHidden(_FORM_COL)
    assert session_state.load_curator_columns_for(second.table.columnCount()) is not None


def test_the_word_cell_carries_the_text_form_when_it_differs(qtbot):
    word = _verb()
    assert word.mined_form == "食べる"
    dialog = WordCurationDialog([word])
    qtbot.addWidget(dialog)

    cell = dialog.table.item(0, _WORD_COL)
    assert cell.text() == "食べる"
    assert cell.data(module._FORM_ROLE) == "食べた"


def test_the_grey_form_shows_only_while_form_in_text_is_hidden(qtbot):
    dialog = WordCurationDialog([_verb()])
    qtbot.addWidget(dialog)
    delegate = dialog.table.itemDelegateForColumn(_WORD_COL)
    assert isinstance(delegate, module._WordFormDelegate)
    index = dialog.table.model().index(0, _WORD_COL)
    option = QStyleOptionViewItem()
    option.font = dialog.table.font()

    assert delegate._form(index) == "食べた"
    wide = delegate.sizeHint(option, index).width()

    dialog.table.setColumnHidden(_FORM_COL, False)
    assert delegate._form(index) == ""
    assert delegate.sizeHint(option, index).width() < wide


def test_the_word_starts_at_qts_own_text_inset_and_the_form_follows_it(qtbot):
    """B1.8: the custom paint lines the word up with every plain cell's text.

    The stock delegate paints the same cell's text (the mined word alone); the
    word must start on the same column, and the grey form must extend the ink.
    """
    dialog = WordCurationDialog([_verb()])
    qtbot.addWidget(dialog)
    index = dialog.table.model().index(0, _WORD_COL)
    delegate = dialog.table.itemDelegateForColumn(_WORD_COL)
    assert delegate._form(index) == "食べた"
    stock = QStyledItemDelegate()

    with_form = ink_span(paint_cell(delegate, dialog.table, index))
    word_only = ink_span(paint_cell(stock, dialog.table, index))

    assert with_form[0] == word_only[0] > 0
    assert with_form[1] > word_only[1]


def test_a_noun_carries_no_grey_form(qtbot):
    noun = TokenizedWord(
        surface="雨", lemma="雨", reading="アメ", sentence="雨です", start_time=0.0, end_time=1.0, duration=1.0
    )
    dialog = WordCurationDialog([noun])
    qtbot.addWidget(dialog)
    assert dialog.table.item(0, _WORD_COL).data(module._FORM_ROLE) == ""

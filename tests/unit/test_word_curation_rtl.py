"""Word Curator on an rtl mining language: mined content lays out right to left.

Arabic literals are \\N{} escapes on purpose (see test_content_cell_direction).
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStyledItemDelegate

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.content_text import ContentCellDelegate
from anki_miner.gui.widgets.dialogs import word_curation_dialog as module
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.languages.registry import get_profile
from anki_miner.models import TokenizedWord
from tests.unit._cell_paint import ink_span, paint_cell

AR = get_profile("ar").content_style
JA = get_profile("ja").content_style
HADHA = "\N{ARABIC LETTER HEH}\N{ARABIC LETTER THAL}\N{ARABIC LETTER ALEF}"
KITAB = "\N{ARABIC LETTER KAF}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"
KUTUB = "\N{ARABIC LETTER KAF}\N{ARABIC LETTER TEH}\N{ARABIC LETTER BEH}"
_WORD_COL, _SENTENCE_COL = 1, 4


@pytest.fixture(autouse=True)
def _isolated_ui_state(tmp_path, monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")


def _word(sentence: str, *, start: float = 0.0) -> TokenizedWord:
    return TokenizedWord(
        surface=KITAB,
        lemma=KITAB,
        reading="",
        sentence=sentence,
        start_time=start,
        end_time=start + 1.0,
        duration=1.0,
    )


def _painted_span(dialog: WordCurationDialog, column: int, text: str) -> tuple[int, int]:
    table = dialog.table
    row = next(r for r in range(table.rowCount()) if table.item(r, column).text() == text)
    index = table.model().index(row, column)
    return ink_span(paint_cell(table.itemDelegateForIndex(index), table, index))


def test_an_arabic_sentence_ends_at_its_logical_end(qtbot):
    """Review Focus #4: the final period lands at the left; the right edge holds."""
    bare = f"{HADHA} {KITAB}"
    dialog = WordCurationDialog([_word(bare), _word(bare + ".", start=1.0)], content_style=AR)
    qtbot.addWidget(dialog)

    bare_span = _painted_span(dialog, _SENTENCE_COL, bare)
    stopped_span = _painted_span(dialog, _SENTENCE_COL, bare + ".")

    assert stopped_span[1] == bare_span[1]
    assert stopped_span[0] < bare_span[0]


@pytest.mark.parametrize("column", (2, 3, 4), ids=["form", "reading", "sentence"])
def test_a_japanese_curators_content_cells_paint_as_before(qtbot, column):
    word = TokenizedWord(
        surface="食べた",
        lemma="食べる",
        reading="タベタ",
        sentence="ご飯を食べた。",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="動詞",
    )
    dialog = WordCurationDialog([word], content_style=JA)
    qtbot.addWidget(dialog)
    table = dialog.table
    index = table.model().index(0, column)

    assert paint_cell(table.itemDelegateForIndex(index), table, index) == paint_cell(
        QStyledItemDelegate(), table, index
    )


def test_an_arabic_word_leads_from_the_right_and_its_form_follows_leftwards(qtbot):
    dialog = WordCurationDialog([_word(f"{HADHA} {KITAB}.")], content_style=AR)
    qtbot.addWidget(dialog)
    table = dialog.table
    index = table.model().index(0, _WORD_COL)
    table.item(0, _WORD_COL).setData(module._FORM_ROLE, KUTUB)
    delegate = table.itemDelegateForIndex(index)
    assert delegate._form(index) == KUTUB

    with_form = ink_span(paint_cell(delegate, table, index))
    word_only = ink_span(paint_cell(ContentCellDelegate(table, lambda: AR), table, index))

    assert with_form[1] == word_only[1], "the word left the leading (right) edge"
    assert with_form[0] < word_only[0], "the grey form is not to the word's left"


def test_the_sentence_picker_takes_the_content_direction(qtbot):
    word = _word(f"{HADHA} {KITAB}.")
    word.sentence_candidates = [word, _word(f"{KITAB} {HADHA}.", start=5.0)]
    dialog = WordCurationDialog([word], content_style=AR)
    qtbot.addWidget(dialog)

    assert dialog.sentence_list.layoutDirection() == Qt.LayoutDirection.RightToLeft


def test_a_japanese_sentence_picker_is_never_touched(qtbot):
    word = TokenizedWord(
        surface="本", lemma="本", reading="ホン", sentence="本を読む。", start_time=0.0, end_time=1.0, duration=1.0
    )
    other = TokenizedWord(
        surface="本", lemma="本", reading="ホン", sentence="本がある。", start_time=5.0, end_time=6.0, duration=1.0
    )
    word.sentence_candidates = [word, other]
    dialog = WordCurationDialog([word], content_style=JA)
    qtbot.addWidget(dialog)

    assert not dialog.sentence_list.testAttribute(Qt.WidgetAttribute.WA_SetLayoutDirection)

"""Tests for the WordCurationDialog "Occurrences" column (Issue #88)."""

from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord

# Column index of the Occurrences column (last, after Freq. Rank).
_OCC_COL = 6


def _word(lemma: str, occurrence_count: int) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        occurrence_count=occurrence_count,
    )


def test_occurrences_column_present(qtbot):
    """The table gains a 7th 'Occurrences' column."""
    dlg = WordCurationDialog([_word("食べる", 3)])
    qtbot.addWidget(dlg)

    assert dlg.table.columnCount() == 7
    header = dlg.table.horizontalHeaderItem(_OCC_COL)
    assert header is not None
    assert header.text() == "Occurrences"


def test_occurrence_count_rendered(qtbot):
    """Each row shows the word's occurrence_count."""
    dlg = WordCurationDialog([_word("食べる", 15), _word("猫", 0)])
    qtbot.addWidget(dlg)

    texts = {dlg.table.item(r, _OCC_COL).text() for r in range(dlg.table.rowCount())}
    assert texts == {"15", "0"}


def test_occurrences_sort_numerically(qtbot):
    """Sorting on the column orders 15 above 2, not lexically (2 above 15)."""
    dlg = WordCurationDialog([_word("a", 2), _word("b", 15), _word("c", 9)])
    qtbot.addWidget(dlg)

    dlg.table.sortItems(_OCC_COL, Qt.SortOrder.DescendingOrder)

    ordered = [int(dlg.table.item(r, _OCC_COL).text()) for r in range(dlg.table.rowCount())]
    assert ordered == [15, 9, 2]

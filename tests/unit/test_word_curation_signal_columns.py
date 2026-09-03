"""Tests for the curator's two sentence-signal columns.

"Unknowns in line" is the raw i+1 signal -- 1 means the line has exactly one
unknown word -- and "Sentence length" is the character count. Both sort
numerically and both follow the sentence the user picks.
"""

from __future__ import annotations

from anki_miner.gui.utils.qt_helpers import SORT_ROLE
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord

_UNKNOWNS_COL = 7
_LENGTH_COL = 8


def _word(lemma: str, sentence: str, unknowns: int, candidates=()) -> TokenizedWord:
    word = TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence=sentence,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="動詞",
        line_unknown_count=unknowns,
    )
    word.sentence_candidates = list(candidates)
    return word


def test_the_two_signal_columns_are_present(qtbot):
    dlg = WordCurationDialog([_word("食べる", "猫が魚を食べた", 2)])
    qtbot.addWidget(dlg)

    assert dlg.table.columnCount() == 10
    assert dlg.table.horizontalHeaderItem(_UNKNOWNS_COL).text() == "Unknowns in line"
    assert dlg.table.horizontalHeaderItem(_LENGTH_COL).text() == "Sentence length"


def test_the_cells_show_the_count_and_the_character_length(qtbot):
    dlg = WordCurationDialog([_word("食べる", "猫が魚を食べた", 2)])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, _UNKNOWNS_COL).text() == "2"
    assert dlg.table.item(0, _LENGTH_COL).text() == "7"


def test_both_columns_sort_numerically(qtbot):
    """15 must rank above 2, not below it as a string would."""
    dlg = WordCurationDialog([_word("食べる", "あ" * 15, 3)])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, _UNKNOWNS_COL).data(SORT_ROLE) == 3.0
    assert dlg.table.item(0, _LENGTH_COL).data(SORT_ROLE) == 15.0


def test_an_uncomputed_count_shows_a_dash_and_sorts_last(qtbot):
    """0 means "not computed" -- it must not sort above a real i+1 line."""
    dlg = WordCurationDialog([_word("食べる", "猫が魚を食べた", 0)])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, _UNKNOWNS_COL).text() == "-"
    assert dlg.table.item(0, _UNKNOWNS_COL).data(SORT_ROLE) == float("inf")


def test_the_header_explains_what_sorting_ascending_gives_you(qtbot):
    dlg = WordCurationDialog([_word("食べる", "猫が魚を食べた", 2)])
    qtbot.addWidget(dlg)

    assert "i+1" in dlg.table.horizontalHeaderItem(_UNKNOWNS_COL).toolTip()


def test_both_columns_follow_the_picked_sentence(qtbot):
    """A pick repaints the row; a stale signal column is the Issue #108 failure."""
    picked = _word("食べる", "魚だ", 1)
    word = _word(
        "食べる",
        "猫が魚を食べた",
        2,
        candidates=[_word("食べる", "猫が魚を食べた", 2), picked],
    )
    dlg = WordCurationDialog([word])
    qtbot.addWidget(dlg)

    dlg._apply_pick_to_row(0, picked)

    assert dlg.table.item(0, _UNKNOWNS_COL).text() == "1"
    assert dlg.table.item(0, _LENGTH_COL).text() == "2"

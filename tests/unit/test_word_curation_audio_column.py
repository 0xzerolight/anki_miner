"""Tests for the Word Curator's Audio column (FUTURE_IDEAS item 4)."""

from __future__ import annotations

from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs.word_curation_dialog import AUDIO_COLUMN, WordCurationDialog
from anki_miner.models import TokenizedWord


def _word(lemma: str = "食べる", available: bool | None = None) -> TokenizedWord:
    word = TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="たべる",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )
    word.expression_audio_available = available
    return word


def _fetch(_word, _cancelled_check=None) -> bool:
    return True


def test_column_is_hidden_without_expression_audio(qtbot):
    dlg = WordCurationDialog([_word()])
    qtbot.addWidget(dlg)

    assert dlg.table.columnCount() == 11
    assert dlg.table.isColumnHidden(AUDIO_COLUMN)
    assert AUDIO_COLUMN not in dlg._column_menu_actions()


def test_column_is_shown_when_the_run_mines_expression_audio(qtbot):
    dlg = WordCurationDialog([_word()], expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)

    assert not dlg.table.isColumnHidden(AUDIO_COLUMN)
    assert AUDIO_COLUMN in dlg._column_menu_actions()
    assert dlg.table.horizontalHeaderItem(AUDIO_COLUMN).text() == "Audio"


def test_the_column_is_hideable_from_the_header_menu(qtbot):
    dlg = WordCurationDialog([_word()], expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)

    dlg._column_menu_actions()[AUDIO_COLUMN].setChecked(False)

    assert dlg.table.isColumnHidden(AUDIO_COLUMN)


def test_the_header_carries_an_explanatory_tooltip(qtbot):
    dlg = WordCurationDialog([_word()], expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)

    assert "✓" in dlg.table.horizontalHeaderItem(AUDIO_COLUMN).toolTip()


def test_three_states_render_three_glyphs(qtbot):
    dlg = WordCurationDialog(
        [_word("食べる", True), _word("猫", False), _word("犬", None)],
        expression_audio_fetch_fn=_fetch,
    )
    qtbot.addWidget(dlg)

    assert [dlg.table.item(row, AUDIO_COLUMN).text() for row in range(3)] == ["✓", "✗", "-"]


def test_found_sorts_before_missing_and_unknown_sorts_last(qtbot):
    from anki_miner.gui.utils.qt_helpers import SORT_ROLE

    dlg = WordCurationDialog(
        [_word("食べる", True), _word("猫", False), _word("犬", None)],
        expression_audio_fetch_fn=_fetch,
    )
    qtbot.addWidget(dlg)

    keys = [dlg.table.item(row, AUDIO_COLUMN).data(SORT_ROLE) for row in range(3)]
    assert keys[0] < keys[1] < keys[2]


def test_every_cell_carries_its_own_tooltip(qtbot):
    dlg = WordCurationDialog([_word("食べる", True)], expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, AUDIO_COLUMN).toolTip() == "Pronunciation audio found"


def test_set_state_repaints_the_cell_and_stamps_the_word(qtbot):
    words = [_word("食べる", None)]
    dlg = WordCurationDialog(words, expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)

    dlg.set_expression_audio_state(0, True)

    assert dlg.table.item(0, AUDIO_COLUMN).text() == "✓"
    assert words[0].expression_audio_available is True


def test_set_state_finds_the_row_after_a_re_sort(qtbot):
    words = [_word("食べる", None), _word("猫", None)]
    dlg = WordCurationDialog(words, expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)
    dlg.table.sortItems(1, Qt.SortOrder.DescendingOrder)

    dlg.set_expression_audio_state(1, True)

    row = dlg._visual_row_for_index(1)
    assert dlg.table.item(row, AUDIO_COLUMN).text() == "✓"


def test_set_state_is_a_no_op_once_the_dialog_is_closing(qtbot):
    words = [_word("食べる", None)]
    dlg = WordCurationDialog(words, expression_audio_fetch_fn=_fetch)
    qtbot.addWidget(dlg)
    dlg._closing = True

    dlg.set_expression_audio_state(0, True)

    assert dlg.table.item(0, AUDIO_COLUMN).text() == "-"

"""Tests for the WordCurationDialog's column control (reorder, hide, reset)."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray
from PyQt6.QtWidgets import QHeaderView

from anki_miner.gui.utils import session_state
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord

_READING_COL = 3


def _word(lemma: str = "食べる") -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="たべる",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )


def test_sections_are_movable(qtbot):
    dlg = WordCurationDialog([_word()])
    qtbot.addWidget(dlg)

    assert dlg.table.horizontalHeader().sectionsMovable()


def test_header_menu_hides_a_column(qtbot):
    dlg = WordCurationDialog([_word()])
    qtbot.addWidget(dlg)

    actions = dlg._column_menu_actions()
    actions[_READING_COL].setChecked(False)

    assert dlg.table.isColumnHidden(_READING_COL)


def test_the_include_column_is_never_hideable(qtbot):
    """Hiding column 0 would leave no way to include or exclude a word."""
    dlg = WordCurationDialog([_word()])
    qtbot.addWidget(dlg)

    assert 0 not in dlg._column_menu_actions()


def test_reset_unhides_every_column_and_restores_the_order(qtbot):
    dlg = WordCurationDialog([_word()])
    qtbot.addWidget(dlg)
    header = dlg.table.horizontalHeader()
    dlg.table.setColumnHidden(_READING_COL, True)
    header.moveSection(header.visualIndex(1), 4)

    dlg._reset_columns()

    assert not dlg.table.isColumnHidden(_READING_COL)
    assert [header.logicalIndex(v) for v in range(dlg.table.columnCount())] == list(range(dlg.table.columnCount()))
    assert header.sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch


def test_the_arrangement_is_saved_on_close_and_restored_next_time(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")

    first = WordCurationDialog([_word()])
    qtbot.addWidget(first)
    first.table.setColumnHidden(_READING_COL, True)
    first.reject()

    second = WordCurationDialog([_word()])
    qtbot.addWidget(second)

    assert second.table.isColumnHidden(_READING_COL)


def test_a_state_from_a_different_column_count_is_ignored(qtbot, tmp_path, monkeypatch):
    """The stale-arrangement guard, seen from the dialog."""
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")
    session_state.save_curator_columns(QByteArray(b"garbage"), 99)

    dlg = WordCurationDialog([_word()])
    qtbot.addWidget(dlg)

    assert not any(dlg.table.isColumnHidden(c) for c in range(dlg.table.columnCount()))

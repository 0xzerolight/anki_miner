"""Tests for the curator's Translation column (secondary-language subtitles, F7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QWidget

from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    TRANSLATION_COLUMN,
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord

PRIMARY = [(1.0, 3.0, "前の行です"), (5.0, 7.0, "食べるのテスト"), (9.0, 11.0, "次の行です")]
SECONDARY = [(0.5, 3.5, "The line before."), (4.8, 7.2, "A test of eating."), (8.5, 11.0, "The line after.")]


def _word(start: float = 5.0, sentence: str = "食べるのテスト") -> TokenizedWord:
    return TokenizedWord(
        surface="食べた",
        lemma="食べる",
        reading="タベル",
        sentence=sentence,
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="動詞",
    )


def _dialog(qtbot, tmp_path, *, secondary=SECONDARY, secondary_offset=0.0):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"\x00")
    ctx = CurationMediaContext(
        video_file=video,
        subtitle_entries=list(PRIMARY),
        secondary_entries=list(secondary),
        secondary_offset=secondary_offset,
    )
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
        dlg = WordCurationDialog([_word()], media_context=ctx)
    qtbot.addWidget(dlg)
    dlg.player_widget = MagicMock()
    return dlg


def _focus(dlg: WordCurationDialog, row: int) -> None:
    dlg.table.setCurrentCell(row, 0)
    dlg._on_row_focus_changed()
    dlg._focus_timer.stop()
    dlg._on_focus_timer_fired()


def test_column_is_hidden_and_unlisted_without_a_second_track(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path, secondary=[])
    assert dlg.table.columnCount() == 10
    assert dlg.table.isColumnHidden(TRANSLATION_COLUMN)
    assert TRANSLATION_COLUMN not in dlg._column_menu_actions()


def test_column_shows_the_overlapping_secondary_line(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    assert not dlg.table.isColumnHidden(TRANSLATION_COLUMN)
    assert TRANSLATION_COLUMN in dlg._column_menu_actions()
    assert dlg.table.horizontalHeaderItem(TRANSLATION_COLUMN).text() == "Translation"
    assert dlg.table.item(0, TRANSLATION_COLUMN).text() == "A test of eating."


def test_the_secondary_offset_moves_the_match(qtbot, tmp_path):
    # +4.0 s puts "The line before." (0.5-3.5) at 4.5-7.5, over the word's 5.0-7.0.
    dlg = _dialog(qtbot, tmp_path, secondary=[(0.5, 3.5, "The line before.")], secondary_offset=4.0)
    assert dlg.table.item(0, TRANSLATION_COLUMN).text() == "The line before."


def test_a_line_expansion_widens_the_translation(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    _focus(dlg, 0)
    dlg._on_expand_line(-1)
    assert dlg.table.item(0, TRANSLATION_COLUMN).text() == "The line before. A test of eating."
    dlg._on_expand_reset()
    assert dlg.table.item(0, TRANSLATION_COLUMN).text() == "A test of eating."


def test_reset_columns_keeps_the_forced_hide(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path, secondary=[])
    dlg._reset_columns()
    assert dlg.table.isColumnHidden(TRANSLATION_COLUMN)


def test_search_matches_the_translation_column(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    dlg.search_input.setText("eating")
    dlg._apply_search()
    assert not dlg.table.isRowHidden(0)

    dlg.search_input.setText("zzz")
    dlg._apply_search()
    assert dlg.table.isRowHidden(0)


def test_search_ignores_the_translation_column_without_a_second_track(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path, secondary=[])
    dlg.search_input.setText("eating")
    dlg._apply_search()
    assert dlg.table.isRowHidden(0)


def test_the_player_receives_the_second_track(qtbot, tmp_path):
    class _Stub(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.set_source = MagicMock()

    stub = _Stub()
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"\x00")
    ctx = CurationMediaContext(
        video_file=video, subtitle_entries=list(PRIMARY), secondary_entries=list(SECONDARY), secondary_offset=0.5
    )
    with patch("anki_miner.gui.widgets.subtitle_player_widget.SubtitlePlayerWidget", return_value=stub):
        dlg = WordCurationDialog([_word()], media_context=ctx)
    qtbot.addWidget(dlg)
    stub.set_source.assert_called_once_with(
        video, PRIMARY, 0.0, audio_track_override=None, secondary_entries=SECONDARY, secondary_offset=0.5
    )

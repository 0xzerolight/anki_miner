"""Tests for the word curator's "edit word and sentence" wiring.

The editor window itself is covered by ``test_sentence_edit_dialog.py``; this
module owns the curator side — which index an edit lands on, how the row and
the panes follow, what invalidates it, and what ``get_selected_words`` stamps.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QWidget

from anki_miner.gui.widgets.dialogs.sentence_edit_dialog import SentenceEditDialog
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import SentenceEdit, TokenizedWord

ORIGINAL = "時給系のスポーツは本当に苦手"
EDITED = "持久系のスポーツは本当に苦手"
ENTRIES = [(1.0, 3.0, "前の行です"), (5.0, 7.0, ORIGINAL), (9.0, 11.0, "次の行です"), (20.0, 22.0, "走るのテスト")]


def _token(surface: str, start: int, sentence: str, *, reading: str = "") -> TokenizedWord:
    return TokenizedWord(
        surface=surface,
        lemma=surface,
        reading=reading,
        sentence=sentence,
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        pos="名詞",
        surface_start=start,
        surface_end=start + len(surface),
        expression_reading=reading,
        mined_form_override=surface,
    )


def _parse(text: str) -> list[TokenizedWord]:
    """A stand-in parser: every known word found anywhere in ``text``, in text order.

    Find-based rather than prefix-based so the merged-line cases (``前の行です
    持久系…``) still list the corrected word at its real offset.
    """
    tokens = []
    for surface, reading in (("時給", "じきゅう"), ("持久", "じきゅう"), ("スポーツ", ""), ("走る", "")):
        at = text.find(surface)
        if at >= 0:
            tokens.append(_token(surface, at, text, reading=reading))
    tokens.sort(key=lambda t: t.surface_start)
    return tokens


def _make_word(surface: str = "時給", start_time: float = 5.0, sentence: str = ORIGINAL, **kwargs) -> TokenizedWord:
    return TokenizedWord(
        surface=surface,
        lemma=surface,
        reading="ジキュウ",
        sentence=sentence,
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos="名詞",
        surface_start=0,
        surface_end=len(surface),
        mined_form_override=surface,
        frequency_rank=kwargs.pop("frequency_rank", 4200),
        **kwargs,
    )


@pytest.fixture()
def words():
    # 時 (U+6642) sorts before 走 (U+8D70): a DESCENDING Word-column sort is what
    # moves row 0, which the across-a-sort tests need to be non-vacuous.
    return [_make_word("時給", start_time=5.0), _make_word("走る", start_time=20.0, sentence="走るのテスト")]


@pytest.fixture()
def existing_video(tmp_path) -> Path:
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    return video


@pytest.fixture()
def sync_off_thread(monkeypatch):
    import anki_miner.gui.widgets.dialogs.sentence_edit_dialog as sed

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        on_done(work())
        return MagicMock()

    monkeypatch.setattr(sed, "run_off_thread", fake_run_off_thread)


def _dialog(qtbot, words, video=None, *, parse=_parse, **ctx_kwargs) -> WordCurationDialog:
    if video is None:
        dlg = WordCurationDialog(words, parse_sentence_fn=parse)
        qtbot.addWidget(dlg)
        return dlg
    real_stub = QWidget()
    ctx = CurationMediaContext(video_file=video, subtitle_entries=list(ENTRIES), audio_padding=0.3, **ctx_kwargs)
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=real_stub):
        dlg = WordCurationDialog(words, media_context=ctx, parse_sentence_fn=parse)
    qtbot.addWidget(dlg)
    dlg.player_widget = MagicMock()
    return dlg


def _focus(dialog: WordCurationDialog, row: int) -> None:
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


def _edit(dialog: WordCurationDialog, idx: int, text: str = EDITED, pick: str = "持久") -> None:
    """Open the editor for ``idx``, retype, pick ``pick``, confirm."""
    dialog._edit_word(idx)
    editor = dialog._sentence_editor
    assert isinstance(editor, SentenceEditDialog)
    editor.sentence_edit.setPlainText(text)
    editor._request_parse()
    rows = [editor.word_list.item(i).text() for i in range(editor.word_list.count())]
    editor.word_list.setCurrentRow(next(i for i, r in enumerate(rows) if r.startswith(pick)))
    editor.accept()


def _check_all(dialog: WordCurationDialog) -> None:
    for row in range(dialog.table.rowCount()):
        dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)


def _cell(dialog: WordCurationDialog, idx: int, column: int) -> str:
    row = dialog._visual_row_for_index(idx)
    return dialog.table.item(row, column).text()


class TestAvailability:
    def test_no_parser_means_no_editor(self, qtbot, words):
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        dlg._edit_word(0)
        assert dlg._sentence_editor is None
        assert dlg._sentence_edits == {}

    def test_double_click_on_the_checkbox_column_does_not_edit(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        dlg._on_cell_double_clicked(0, 0)
        assert dlg._sentence_editor is None

    def test_double_click_on_a_cell_opens_the_editor_for_that_word(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        dlg.table.sortItems(1, Qt.SortOrder.DescendingOrder)  # 走る now sits on visual row 0
        row = dlg._visual_row_for_index(1)
        dlg._on_cell_double_clicked(row, 4)
        editor = dlg._sentence_editor
        assert editor is not None
        assert editor.sentence_edit.toPlainText() == "走るのテスト"
        editor.reject()

    def test_f2_edits_the_focused_word(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _focus(dlg, 1)
        dlg._edit_focused_word()
        editor = dlg._sentence_editor
        assert editor is not None
        assert editor.sentence_edit.toPlainText() == "走るのテスト"
        editor.reject()

    def test_only_one_editor_at_a_time(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        dlg._edit_word(0)
        first = dlg._sentence_editor
        dlg._edit_word(1)
        assert dlg._sentence_editor is first
        first.reject()

    def test_cancel_records_nothing(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        dlg._edit_word(0)
        dlg._sentence_editor.reject()
        assert dlg._sentence_edits == {}
        assert dlg._sentence_editor is None


class TestRecording:
    def test_edit_records_against_the_original_index_across_a_sort(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        dlg.table.sortItems(1, Qt.SortOrder.DescendingOrder)
        assert dlg._visual_row_for_index(0) == 1, "the sort must actually move the word"
        _edit(dlg, 0)
        assert list(dlg._sentence_edits) == [0]
        assert dlg._sentence_edits[0].mined_form == "持久"

    def test_row_repaints_from_the_edited_token(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        assert _cell(dlg, 0, 1) == "持久"
        assert _cell(dlg, 0, 2) == "持久"
        assert _cell(dlg, 0, 3) == "じきゅう"  # column 3 prints the token's ``reading``
        assert _cell(dlg, 0, 4) == EDITED
        assert _cell(dlg, 0, 8) == str(len(EDITED))

    def test_frequency_rank_shows_unranked_after_an_edit(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        assert _cell(dlg, 0, 5) == "4200"
        _edit(dlg, 0)
        assert _cell(dlg, 0, 5) == "-"

    def test_definition_pane_follows_the_edit(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        with patch.object(dlg, "_refresh_definition") as refresh:
            _edit(dlg, 0)
        assert refresh.call_args[0][0].mined_form == "持久"

    def test_refocusing_the_row_keeps_the_edited_definition(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        with patch.object(dlg, "_refresh_definition") as refresh:
            _focus(dlg, 1)
            _focus(dlg, 0)
        assert refresh.call_args[0][0].mined_form == "持久"

    def test_re_editing_seeds_from_the_existing_edit(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        dlg._edit_word(0)
        editor = dlg._sentence_editor
        assert editor.sentence_edit.toPlainText() == EDITED
        assert editor.result_word().mined_form == "持久"
        editor.reject()

    def test_reset_restores_the_row(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        dlg._reset_sentence_edit(0)
        assert dlg._sentence_edits == {}
        assert _cell(dlg, 0, 1) == "時給"
        assert _cell(dlg, 0, 4) == ORIGINAL
        assert _cell(dlg, 0, 5) == "4200"

    def test_copy_word_and_sentence_read_the_edit(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        assert dlg._shown_word(0).mined_form == "持久"
        assert dlg._shown_word(0).sentence == EDITED


class TestInvalidation:
    @pytest.fixture()
    def picker_words(self):
        first = _make_word("時給", start_time=5.0)
        second = _make_word("時給", start_time=40.0, sentence="二つ目の時給")
        primary = _make_word("時給", start_time=5.0)
        primary.sentence_candidates = [first, second]
        return [primary]

    def test_a_sentence_pick_drops_the_edit(self, qtbot, picker_words, existing_video, sync_off_thread):
        dlg = _dialog(qtbot, picker_words, existing_video)
        _focus(dlg, 0)
        _edit(dlg, 0)
        dlg._on_candidate_chosen(1)
        assert dlg._sentence_edits == {}
        assert _cell(dlg, 0, 4).startswith("二つ目の時給")  # plus the "(2)" candidate badge

    def test_expansion_buttons_disable_while_an_edit_exists(self, qtbot, words, existing_video, sync_off_thread):
        dlg = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        assert dlg.expand_prev_button.isEnabled()
        _edit(dlg, 0)
        assert not dlg.expand_prev_button.isEnabled()
        assert not dlg.expand_next_button.isEnabled()

    def test_editing_an_expanded_line_seeds_the_merged_text(self, qtbot, words, existing_video, sync_off_thread):
        dlg = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg._on_expand_line(-1)
        dlg._edit_word(0)
        editor = dlg._sentence_editor
        assert editor.sentence_edit.toPlainText() == f"前の行です {ORIGINAL}"
        editor.reject()

    def test_reset_lines_drops_the_edit_with_the_expansion(self, qtbot, words, existing_video, sync_off_thread):
        dlg = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg._on_expand_line(-1)
        _edit(dlg, 0, text=f"前の行です {EDITED}")
        assert 0 in dlg._sentence_edits
        dlg._on_expand_reset()
        assert dlg._sentence_edits == {}
        assert _cell(dlg, 0, 4) == ORIGINAL


class TestSelection:
    def test_untouched_words_carry_no_edit(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _check_all(dlg)
        assert [w.sentence_edit for w in dlg.get_selected_words()] == [None, None]

    def test_edited_word_is_stamped_with_the_tokens_span(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        _check_all(dlg)
        selected = dlg.get_selected_words()
        by_form = {w.mined_form: w for w in selected}
        assert by_form["時給"].sentence_edit == SentenceEdit(text=EDITED, target_start=0, target_end=2)
        assert by_form["走る"].sentence_edit is None

    def test_edit_composes_with_an_expansion(self, qtbot, words, existing_video, sync_off_thread):
        dlg = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg._on_expand_line(-1)
        _edit(dlg, 0, text=f"前の行です {EDITED}")
        _check_all(dlg)
        word = next(w for w in dlg.get_selected_words() if w.mined_form == "時給")
        assert word.line_expansion == (1, 0)
        assert word.sentence_edit is not None
        assert word.sentence_edit.text.endswith(EDITED)

    def test_source_word_is_not_mutated(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        _check_all(dlg)
        dlg.get_selected_words()
        assert words[0].sentence_edit is None
        assert words[0].sentence == ORIGINAL

    def test_unchecked_edited_word_is_not_returned(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        _edit(dlg, 0)
        dlg.table.item(dlg._visual_row_for_index(0), 0).setCheckState(Qt.CheckState.Unchecked)
        assert [w.mined_form for w in dlg.get_selected_words()] == ["走る"]


class TestTeardown:
    def test_closing_the_curator_closes_an_open_editor(self, qtbot, words, sync_off_thread):
        dlg = _dialog(qtbot, words)
        dlg._edit_word(0)
        editor = dlg._sentence_editor
        dlg.reject()
        assert dlg._sentence_editor is None
        assert editor.result() == QDialog.DialogCode.Rejected

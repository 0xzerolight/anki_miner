"""Tests for the WordCurationDialog sentence picker.

Covers picking which example sentence (and scene) gets mined when a word
appears on multiple subtitle lines:
1. No picker for single-occurrence words.
2. Focusing a multi-candidate word populates the list, default-selecting the
   current pick.
3. Activating another candidate updates the chosen word, the Sentence cell, and
   seeks the player.
4. get_selected_words returns the chosen variant (original when untouched).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord


def _leaf(lemma: str, sentence: str, start_time: float) -> TokenizedWord:
    """A candidate variant (no nested candidates)."""
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence=sentence,
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos="動詞",
    )


def _word_with_candidates() -> TokenizedWord:
    """A word that appears on three lines; current pick = the first."""
    cands = [
        _leaf("食べる", "朝ごはんを食べる", 1.0),
        _leaf("食べる", "パンを食べる", 5.0),
        _leaf("食べる", "早く食べなさい", 9.0),
    ]
    word = _leaf("食べる", "朝ごはんを食べる", 1.0)
    word.sentence_candidates = cands
    return word


def _plain_word() -> TokenizedWord:
    """A single-occurrence word (no candidates)."""
    return _leaf("走る", "公園を走る", 20.0)


def _select_and_fire(dialog: WordCurationDialog, row: int) -> None:
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


@pytest.fixture()
def mixed_words():
    # Row 0 has candidates, row 1 does not.
    return [_word_with_candidates(), _plain_word()]


class TestPickerVisibility:
    def test_no_picker_without_candidates(self, qtbot):
        dlg = WordCurationDialog([_plain_word()])
        qtbot.addWidget(dlg)
        assert dlg._has_candidates is False
        assert not hasattr(dlg, "sentence_list")

    def test_picker_present_with_candidates(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        assert dlg._has_candidates is True
        assert hasattr(dlg, "sentence_list")

    def test_sentence_cell_shows_candidate_count(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        row = dlg._visual_row_for_index(0)
        assert row is not None
        assert "(3)" in dlg.table.item(row, 4).text()


class TestPickerPopulation:
    def test_focus_populates_and_selects_current(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        assert dlg.sentence_list.count() == 3
        # Default pick is the first candidate (matches the word's sentence/timing).
        assert dlg.sentence_list.currentRow() == 0
        assert dlg.sentence_list.isEnabled()

    def test_focus_single_occurrence_disables_list(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 1)
        assert dlg.sentence_list.count() == 0
        assert dlg.sentence_list.isEnabled() is False


class TestPickerSelection:
    def test_pick_updates_chosen_and_cell(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)

        dlg.sentence_list.setCurrentRow(1)  # user picks the 2nd sentence

        assert dlg._chosen[0].sentence == "パンを食べる"
        assert dlg._chosen[0].start_time == 5.0
        cell = dlg.table.item(dlg._visual_row_for_index(0), 4)
        assert "パンを食べる" in cell.text()
        assert "(3)" in cell.text()

    def test_get_selected_words_returns_chosen(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(2)

        selected = dlg.get_selected_words()
        # Row 0 word reflects the pick; row 1 untouched word is unchanged.
        chosen = next(w for w in selected if w.lemma == "食べる")
        assert chosen.sentence == "早く食べなさい"
        assert chosen.start_time == 9.0
        plain = next(w for w in selected if w.lemma == "走る")
        assert plain.sentence == "公園を走る"

    def test_untouched_word_returns_original(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        # Never focus/pick — defaults flow through.
        selected = dlg.get_selected_words()
        chosen = next(w for w in selected if w.lemma == "食べる")
        assert chosen.sentence == "朝ごはんを食べる"


class TestPickerPlayerSeek:
    def test_pick_seeks_player_to_chosen_scene(self, qtbot, mixed_words, tmp_path):
        from PyQt6.QtWidgets import QWidget

        video: Path = tmp_path / "v.mkv"
        video.write_bytes(b"")
        ctx = CurationMediaContext(video_file=video, subtitle_entries=[(1.0, 3.0, "x")], offset=0.0)

        real_stub = QWidget()
        with patch.object(WordCurationDialog, "_create_player_widget", return_value=real_stub):
            dlg = WordCurationDialog(mixed_words, media_context=ctx)
        qtbot.addWidget(dlg)
        mock_player = MagicMock()
        dlg.player_widget = mock_player

        _select_and_fire(dlg, 0)
        mock_player.reset_mock()
        dlg.sentence_list.setCurrentRow(2)  # pick the 9.0s scene

        mock_player.seek_seconds.assert_called_with(9.0)

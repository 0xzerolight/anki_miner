"""Tests for the word curator's per-word audio clip override.

The strip itself is covered by ``test_audio_clip_editor.py``; this module owns
the wiring — which index an edit lands on, what a sentence pick does to it, and
what ``get_selected_words`` hands the extraction phase.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.audio_clip_editor import to_ticks
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord

PADDING = 0.3


def _make_word(lemma: str = "食べる", start_time: float = 5.0, **kwargs) -> TokenizedWord:
    return TokenizedWord(
        surface=kwargs.pop("surface", f"{lemma}た"),
        lemma=lemma,
        reading="タベル",
        sentence=kwargs.pop("sentence", f"{lemma}のテスト"),
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos="動詞",
        **kwargs,
    )


@pytest.fixture()
def words():
    return [_make_word("食べる", start_time=5.0), _make_word("走る", start_time=20.0)]


@pytest.fixture()
def existing_video(tmp_path) -> Path:
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    return video


def _dialog(qtbot, words, video, **ctx_kwargs) -> tuple[WordCurationDialog, MagicMock]:
    """Build a curator whose player is a MagicMock (as the media tests do)."""
    from PyQt6.QtWidgets import QWidget

    real_stub = QWidget()
    ctx = CurationMediaContext(
        video_file=video,
        subtitle_entries=[(5.0, 7.0, "食べる")],
        audio_padding=PADDING,
        **ctx_kwargs,
    )
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=real_stub):
        dlg = WordCurationDialog(words, media_context=ctx)
    qtbot.addWidget(dlg)
    mock_player = MagicMock()
    dlg.player_widget = mock_player
    return dlg, mock_player


def _focus(dialog: WordCurationDialog, row: int) -> None:
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


def _drag(dialog: WordCurationDialog, in_seconds: float, out_seconds: float) -> None:
    """Emit what the clip slider emits when the user moves a handle there."""
    dialog.clip_editor.slider.values_changed.emit(to_ticks(in_seconds), to_ticks(out_seconds))


def _check_all(dialog: WordCurationDialog) -> None:
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, 0)
        assert item is not None
        item.setCheckState(Qt.CheckState.Checked)


class TestAvailability:
    def test_strip_present_with_a_player(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert hasattr(dlg, "clip_editor")

    def test_no_strip_on_a_table_only_curator(self, qtbot, words):
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "clip_editor")

    def test_no_strip_for_manga(self, qtbot, words):
        """Reading mining has no video timeline to trim against."""
        unit = MagicMock()
        ctx = CurationMediaContext(video_file=None, subtitle_entries=[], page_units={0: unit})
        dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "clip_editor")

    def test_side_key_unchanged(self, qtbot, words, existing_video):
        """Wrapping the player must not orphan saved side-split blobs."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert dlg._side_key == "player"


class TestSeeding:
    def test_focus_seeds_the_focused_word(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        # 5.0 -> 7.0 widened by 0.3 either side.
        assert dlg.clip_editor.current_window() == (4.7, 7.3)
        assert dlg._clip_index == 0

    def test_focus_follows_the_row(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 1)
        assert dlg.clip_editor.current_window() == (19.7, 22.3)
        assert dlg._clip_index == 1

    def test_scrolling_records_no_override(self, qtbot, words, existing_video):
        """Seeding is not editing — a user who only scrolls mines defaults."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _focus(dlg, 1)
        _focus(dlg, 0)
        assert dlg._clip_overrides == {}

    def test_returning_to_a_row_shows_its_edit(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        _focus(dlg, 1)
        _focus(dlg, 0)
        assert dlg.clip_editor.current_window() == (4.0, 7.3)


class TestOverrideRecording:
    def test_edit_records_against_the_focused_index(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 1)
        _drag(dlg, 19.7, 23.0)
        assert dlg._clip_overrides == {1: (19.7, 23.0)}

    def test_reset_drops_the_override(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        dlg.clip_editor.slider.reset_requested.emit()
        assert dlg._clip_overrides == {}


class TestSelection:
    def test_untouched_words_are_returned_unchanged(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _check_all(dlg)
        assert [w.clip_override for w in dlg.get_selected_words()] == [None, None]

    def test_edited_word_carries_its_window(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        _check_all(dlg)

        selected = dlg.get_selected_words()

        assert selected[0].clip_override == (4.0, 7.3)
        assert selected[1].clip_override is None

    def test_source_word_is_not_mutated(self, qtbot, words, existing_video):
        """Variants are shared with the filter service; the edit rides a copy."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        _check_all(dlg)

        dlg.get_selected_words()

        assert words[0].clip_override is None

    def test_unchecked_edited_word_is_not_returned(self, qtbot, words, existing_video):
        """Editing a clip is not including the word; the checkbox still rules."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        dlg.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)

        selected = dlg.get_selected_words()

        assert [w.lemma for w in selected] == ["走る"]


class TestSentencePick:
    """A pick moves the scene, so a window measured against the old one dies."""

    @pytest.fixture()
    def picker_words(self):
        first = _make_word("食べる", start_time=5.0, sentence="一つ目")
        second = _make_word("食べる", start_time=40.0, sentence="二つ目")
        primary = _make_word("食べる", start_time=5.0, sentence="一つ目")
        primary.sentence_candidates = [first, second]
        return [primary]

    def test_pick_drops_the_override(self, qtbot, picker_words, existing_video):
        dlg, _ = _dialog(qtbot, picker_words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        assert dlg._clip_overrides == {0: (4.0, 7.3)}

        dlg._on_candidate_chosen(1)

        assert dlg._clip_overrides == {}

    def test_pick_reseeds_from_the_new_scene(self, qtbot, picker_words, existing_video):
        dlg, _ = _dialog(qtbot, picker_words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)

        dlg._on_candidate_chosen(1)

        assert dlg.clip_editor.current_window() == (39.7, 42.3)

    def test_pick_selects_the_new_variant_without_an_override(self, qtbot, picker_words, existing_video):
        dlg, _ = _dialog(qtbot, picker_words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        dlg._on_candidate_chosen(1)
        _check_all(dlg)

        selected = dlg.get_selected_words()

        assert selected[0].start_time == 40.0
        assert selected[0].clip_override is None


class TestPlayback:
    def test_play_drives_the_player_range(self, qtbot, words, existing_video):
        dlg, player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.clip_editor.play_button.click()
        player.play_range.assert_called_once_with(4.7, 7.3)

    def test_play_marks_the_button_playing(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.clip_editor.play_button.click()
        assert dlg.clip_editor._playing is True

    def test_second_press_stops(self, qtbot, words, existing_video):
        dlg, player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        player.reset_mock()  # focusing a row previews the scene, which pauses
        dlg.clip_editor.play_button.click()
        dlg.clip_editor.play_button.click()
        player.pause.assert_called_once()

    def test_play_uses_the_edited_window(self, qtbot, words, existing_video):
        dlg, player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        dlg.clip_editor.play_button.click()
        player.play_range.assert_called_once_with(4.0, 7.3)

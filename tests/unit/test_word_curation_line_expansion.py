"""Tests for the word curator's prev/next subtitle-line expansion (Issue #120).

The merge math itself is covered by ``test_word_filter.py``; this module owns
the dialog wiring — button availability and enablement, what a press records,
how the clip strip / sentence cell / preview follow, and what
``get_selected_words`` stamps onto the selection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.audio_clip_editor import MAX_CLIP_SECONDS, to_ticks
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord
from anki_miner.services.word_filter import CUE_JOINER

PADDING = 0.3

#: Three consecutive cues around the first word's line, plus the second word's.
ENTRIES = [
    (1.0, 3.0, "前の行です"),
    (5.0, 7.0, "食べるのテスト"),
    (9.0, 11.0, "次の行です"),
    (20.0, 22.0, "走るのテスト"),
]


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
    """Build a curator whose player is a MagicMock (as the clip-override tests do)."""
    from PyQt6.QtWidgets import QWidget

    real_stub = QWidget()
    ctx = CurationMediaContext(
        video_file=video,
        subtitle_entries=ctx_kwargs.pop("subtitle_entries", list(ENTRIES)),
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
    dialog.clip_editor.slider.values_changed.emit(to_ticks(in_seconds), to_ticks(out_seconds))


def _check_all(dialog: WordCurationDialog) -> None:
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, 0)
        assert item is not None
        item.setCheckState(Qt.CheckState.Checked)


MERGED_PREV = "前の行です" + CUE_JOINER + "食べるのテスト"


class TestAvailability:
    def test_buttons_present_with_player_and_entries(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert hasattr(dlg, "expand_prev_button")
        assert hasattr(dlg, "expand_next_button")
        assert hasattr(dlg, "expand_reset_button")

    def test_no_buttons_without_entries(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video, subtitle_entries=[])
        assert not hasattr(dlg, "expand_prev_button")

    def test_no_buttons_for_manga(self, qtbot, words):
        unit = MagicMock()
        ctx = CurationMediaContext(video_file=None, subtitle_entries=[], page_units={0: unit})
        dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "expand_prev_button")

    def test_side_key_unchanged_by_feature(self, qtbot, words, existing_video):
        """The row lives inside the player pane; saved split blobs stay valid."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert dlg._side_key == "player"

    def test_expansion_tooltips_carry_the_real_ceiling(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        prev_tip = dlg.expand_prev_button.toolTip()
        next_tip = dlg.expand_next_button.toolTip()
        assert str(int(MAX_CLIP_SECONDS)) in prev_tip
        assert str(int(MAX_CLIP_SECONDS)) in next_tip
        assert "30 seconds" not in prev_tip or int(MAX_CLIP_SECONDS) == 30
        assert "30 seconds" not in next_tip or int(MAX_CLIP_SECONDS) == 30


class TestWiring:
    def test_prev_press_records_count(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        assert dlg._line_expansions == {0: (1, 0)}

    def test_presses_accumulate(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        dlg.expand_next_button.click()
        assert dlg._line_expansions == {0: (1, 1)}

    def test_expansion_reseeds_clip_editor_with_merged_window(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        # Merged cues span 1.0 -> 7.0, widened by the padding either side.
        assert dlg.clip_editor.current_window() == (0.7, 7.3)

    def test_expansion_pops_prior_clip_override(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _drag(dlg, 4.0, 7.3)
        dlg.expand_prev_button.click()
        assert dlg._clip_overrides == {}

    def test_drag_after_expansion_records_override(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        _drag(dlg, 0.5, 6.0)
        assert dlg._clip_overrides == {0: (0.5, 6.0)}

    def test_sentence_cell_shows_merged_text(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        row = dlg._visual_row_for_index(0)
        assert dlg.table.item(row, 4).text() == MERGED_PREV

    def test_refocus_previews_expanded_start(self, qtbot, words, existing_video):
        dlg, player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        _focus(dlg, 1)
        player.seek_seconds.reset_mock()
        _focus(dlg, 0)
        player.seek_seconds.assert_called_with(1.0)

    def test_reset_restores_cell_window_and_counts(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        dlg.expand_reset_button.click()
        assert dlg._line_expansions == {}
        assert dlg.clip_editor.current_window() == (4.7, 7.3)
        row = dlg._visual_row_for_index(0)
        assert dlg.table.item(row, 4).text() == "食べるのテスト"

    def test_prev_add_snaps_preview_to_new_start(self, qtbot, words, existing_video):
        dlg, player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        player.seek_seconds.reset_mock()
        dlg.expand_prev_button.click()
        qtbot.waitUntil(lambda: player.seek_seconds.called, timeout=1000)
        player.seek_seconds.assert_called_with(1.0)

    def test_next_add_does_not_snap(self, qtbot, words, existing_video):
        dlg, player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        player.seek_seconds.reset_mock()
        dlg.expand_next_button.click()
        qtbot.wait(50)  # give a stray singleShot the chance to fire
        player.seek_seconds.assert_not_called()

    def test_candidate_pick_resets_expansion(self, qtbot, existing_video):
        first = _make_word("食べる", start_time=5.0, sentence="食べるのテスト")
        second = _make_word("食べる", start_time=40.0, sentence="二つ目")
        primary = _make_word("食べる", start_time=5.0, sentence="食べるのテスト")
        primary.sentence_candidates = [first, second]
        dlg, _ = _dialog(qtbot, [primary], existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        assert dlg._line_expansions == {0: (1, 0)}

        dlg._on_candidate_chosen(1)

        assert dlg._line_expansions == {}


class TestGuardrails:
    def test_prev_disabled_at_first_cue(self, qtbot, existing_video):
        word = _make_word("前", start_time=1.0, sentence="前の行です")
        dlg, _ = _dialog(qtbot, [word], existing_video)
        _focus(dlg, 0)
        assert not dlg.expand_prev_button.isEnabled()
        assert dlg.expand_next_button.isEnabled()

    def test_next_disabled_at_last_cue(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 1)  # 走る sits on the final cue
        assert not dlg.expand_next_button.isEnabled()
        assert dlg.expand_prev_button.isEnabled()

    def test_disabled_when_padded_window_exceeds_cap(self, qtbot, existing_video):
        entries = [(0.0, 2.0, "遠い行です"), (40.0, 42.0, "食べるのテスト")]
        word = _make_word("食べる", start_time=40.0)
        dlg, _ = _dialog(qtbot, [word], existing_video, subtitle_entries=entries)
        _focus(dlg, 0)
        # Merging the previous cue would span 0.0 -> 42.0 (> 30 s padded).
        assert not dlg.expand_prev_button.isEnabled()

    def test_reset_disabled_without_expansion(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        assert not dlg.expand_reset_button.isEnabled()
        dlg.expand_prev_button.click()
        assert dlg.expand_reset_button.isEnabled()

    def test_all_disabled_without_focus(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert not dlg.expand_prev_button.isEnabled()
        assert not dlg.expand_next_button.isEnabled()
        assert not dlg.expand_reset_button.isEnabled()

    def test_reset_enabled_when_cue_unresolvable(self, qtbot, words, existing_video):
        """Stored expansion + unresolvable cue: prev/next disable, Reset stays."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        idx = dlg._pending_index
        dlg._line_expansions[idx] = (1, 0)
        dlg._expansion_entries = lambda chosen: None  # context still in flight
        dlg._refresh_expansion_buttons()
        assert not dlg.expand_prev_button.isEnabled()
        assert not dlg.expand_next_button.isEnabled()
        assert dlg.expand_reset_button.isEnabled()


class TestSelection:
    def test_selected_word_carries_line_expansion(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        _check_all(dlg)

        selected = dlg.get_selected_words()

        assert selected[0].line_expansion == (1, 0)
        assert selected[1].line_expansion == (0, 0)

    def test_expansion_and_override_stamp_together(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        _drag(dlg, 0.5, 6.0)
        _check_all(dlg)

        selected = dlg.get_selected_words()

        assert selected[0].line_expansion == (1, 0)
        assert selected[0].clip_override == (0.5, 6.0)

    def test_source_word_object_not_mutated(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.expand_prev_button.click()
        _check_all(dlg)

        dlg.get_selected_words()

        assert words[0].line_expansion == (0, 0)


class TestSpaceKey:
    """Space is play/pause everywhere in the player pane, including on the
    expansion buttons and the clip slider.

    Issue #120 put the buttons in the player pane but left the Space shortcut
    on ``player_widget``, which is their *sibling*. A ``ModernButton`` inherits
    ``QAbstractButton``'s StrongFocus, so clicking ``+ Next line`` focused it
    and the next Space fell through to ``QAbstractButton::keyPressEvent`` and
    merged another line. The clip slider (StrongFocus, arrows-only
    ``keyPressEvent``) had the same hole, where Space simply went dead.
    """

    @staticmethod
    def _space_shortcuts(widget) -> list:
        """Every Space QShortcut anywhere under ``widget`` (recursive)."""
        from PyQt6.QtGui import QKeySequence, QShortcut

        space = QKeySequence(Qt.Key.Key_Space)
        return [sc for sc in widget.findChildren(QShortcut) if sc.key() == space]

    def test_shortcut_is_parented_to_the_pane_not_the_player(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)

        shortcuts = self._space_shortcuts(dlg.player_pane)

        assert len(shortcuts) == 1
        assert shortcuts[0].parent() is dlg.player_pane
        assert shortcuts[0].context() == Qt.ShortcutContext.WidgetWithChildrenShortcut

    def test_exactly_one_space_shortcut_covers_the_pane(self, qtbot, words, existing_video):
        """Two matching WidgetWithChildren shortcuts in one ancestry chain make
        Qt fire ``activatedAmbiguously`` and nothing else -- Space would die."""
        dlg, _ = _dialog(qtbot, words, existing_video)

        assert len(self._space_shortcuts(dlg.player_pane)) == 1

    def test_space_on_the_next_line_button_plays_instead_of_merging(self, qtbot, words, existing_video):
        """The reported bug, driven through real Qt key dispatch.

        ``shortcut.activated.emit()`` cannot catch this: only a real keypress
        exercises the shortcut-map-versus-``keyPressEvent`` race that the button
        was winning.
        """
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication

        dlg, mock_player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        assert dlg.expand_next_button.isEnabled()
        dlg.show()
        QApplication.setActiveWindow(dlg)
        dlg.expand_next_button.setFocus()
        qtbot.waitUntil(lambda: dlg.expand_next_button.hasFocus(), timeout=1000)

        QTest.keyClick(dlg.expand_next_button, Qt.Key.Key_Space)

        assert dlg._line_expansions == {}
        assert mock_player.toggle_play_pause.called
        dlg.hide()

    def test_space_on_the_prev_line_button_plays_instead_of_merging(self, qtbot, words, existing_video):
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication

        dlg, mock_player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        assert dlg.expand_prev_button.isEnabled()
        dlg.show()
        QApplication.setActiveWindow(dlg)
        dlg.expand_prev_button.setFocus()
        qtbot.waitUntil(lambda: dlg.expand_prev_button.hasFocus(), timeout=1000)

        QTest.keyClick(dlg.expand_prev_button, Qt.Key.Key_Space)

        assert dlg._line_expansions == {}
        assert mock_player.toggle_play_pause.called
        dlg.hide()

    def test_space_on_the_clip_slider_reaches_the_player(self, qtbot, words, existing_video):
        """Same pane, older instance: the slider took focus and ate Space."""
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication

        dlg, mock_player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.show()
        QApplication.setActiveWindow(dlg)
        dlg.clip_editor.slider.setFocus()
        qtbot.waitUntil(lambda: dlg.clip_editor.slider.hasFocus(), timeout=1000)

        QTest.keyClick(dlg.clip_editor.slider, Qt.Key.Key_Space)

        assert mock_player.toggle_play_pause.called
        dlg.hide()

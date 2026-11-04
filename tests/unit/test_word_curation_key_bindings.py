"""The Word Curator's keys follow Settings -> Keyboard (config.key_bindings).

Real key presses wherever scope matters: an emitted ``activated`` cannot show
that a key typed into Search stays a letter, or that two same-key shortcuts in
one focus chain cancel each other out (Qt's activatedAmbiguously, #120).
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext, WordCurationDialog
from anki_miner.models import TokenizedWord

PORTABLE = QKeySequence.SequenceFormat.PortableText

#: Three consecutive cues; the word below sits on the middle one, so both
#: line-expansion buttons are live (as in test_word_curation_line_expansion).
ENTRIES = [(1.0, 3.0, "前の行です"), (5.0, 7.0, "食べるのテスト"), (9.0, 11.0, "次の行です")]


def _word(lemma: str, sentence: str, start: float) -> TokenizedWord:
    return TokenizedWord(
        surface=f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=sentence,
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="動詞",
    )


def _curator(qtbot, words, key_bindings=None) -> WordCurationDialog:
    """A table-only curator with a commit callback, so the known verb is live."""
    dialog = WordCurationDialog(words, commit_known_callback=lambda forms: len(forms), key_bindings=key_bindings)
    qtbot.addWidget(dialog)
    return dialog


def _shortcuts(widget: QWidget, text: str) -> list[QShortcut]:
    """Shortcuts parented directly to ``widget`` for the key ``text``."""
    key = QKeySequence.fromString(text, PORTABLE)
    return [
        shortcut
        for shortcut in widget.findChildren(QShortcut, options=Qt.FindChildOption.FindDirectChildrenOnly)
        if shortcut.key() == key
    ]


def _table_shortcut(dialog: WordCurationDialog, text: str) -> QShortcut | None:
    found = _shortcuts(dialog.table, text)
    return found[0] if found else None


def _highlighted(dialog: WordCurationDialog) -> list[int]:
    selection = dialog.table.selectionModel()
    assert selection is not None
    return sorted({index.row() for index in selection.selectedRows()})


def _activate(qtbot, dialog: WordCurationDialog, focus: QWidget) -> None:
    dialog.show()
    qtbot.waitExposed(dialog)
    QApplication.setActiveWindow(dialog)
    focus.setFocus()
    qtbot.waitUntil(focus.hasFocus, timeout=1000)


def test_an_old_config_marks_known_with_d_and_k_does_nothing(qtbot, make_tokenized_words) -> None:
    """Review Focus 5: a gui_config.json written before key_bindings existed."""
    GUIConfigManager.CONFIG_FILE.write_text(json.dumps({"anki_deck_name": "Old deck"}), encoding="utf-8")
    config = GUIConfigManager.load_config()
    assert config.key_bindings == {}

    dialog = _curator(qtbot, make_tokenized_words(3), config.key_bindings)
    assert _table_shortcut(dialog, "K") is None
    first = dialog.table.item(0, 1).text()
    dialog.table.setCurrentCell(0, 1)
    _activate(qtbot, dialog, dialog.table)

    QTest.keyClick(dialog.table, Qt.Key.Key_D)
    assert dialog.pending_known_forms() == {first}

    # Were K still the known key, this second press on the staged row would unstage it.
    QTest.keyClick(dialog.table, Qt.Key.Key_K)
    assert dialog.pending_known_forms() == {first}
    dialog.hide()


def test_typing_curator_keys_into_search_types_letters_and_triggers_nothing(qtbot, make_tokenized_words) -> None:
    """Review Focus 1: every letter key is table-scoped, so Search keeps its letters."""
    dialog = _curator(qtbot, make_tokenized_words(3), {"curator.next_word": "J", "curator.previous_word": "K"})
    for key in ("S", "D", "J", "K", "I", "O"):
        shortcut = _table_shortcut(dialog, key)
        assert shortcut is not None, key  # non-vacuous: each key IS bound, on the table
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    dialog.table.setCurrentCell(1, 1)
    states = [dialog.table.item(row, 0).checkState() for row in range(3)]
    _activate(qtbot, dialog, dialog.search_input)

    QTest.keyClicks(dialog.search_input, "sdjkio")

    assert dialog.search_input.text() == "sdjkio"
    assert dialog.pending_known_forms() == set()
    assert [dialog.table.item(row, 0).checkState() for row in range(3)] == states
    assert dialog.table.currentRow() == 1
    dialog.hide()


def _four_pane_curator(qtbot, tmp_path: Path, key_bindings) -> WordCurationDialog:
    """Table, player pane (stub player), sentence picker and dictionary pane."""
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    first = _word("食べる", "朝ごはんを食べる", 1.0)
    first.sentence_candidates = [_word("食べる", "朝ごはんを食べる", 1.0), _word("食べる", "パンを食べる", 5.0)]
    context = CurationMediaContext(video_file=video, subtitle_entries=[(1.0, 3.0, "朝ごはんを食べる")])
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
        dialog = WordCurationDialog(
            [first, _word("走る", "走るのテスト", 9.0)],
            media_context=context,
            lookup_fn=lambda *args, **kwargs: [],
            key_bindings=key_bindings,
        )
    qtbot.addWidget(dialog)
    dialog._focus_timer.stop()  # no off-thread lookup may start in this test
    return dialog


def test_rebound_play_pause_leaves_space_unbound_on_every_pane(qtbot, tmp_path) -> None:
    """Review Focus 2, structure: P replaces Space at every install site, once each."""
    dialog = _four_pane_curator(qtbot, tmp_path, {"curator.play_pause": "P"})

    space = QKeySequence.fromString("Space", PORTABLE)
    assert [shortcut for shortcut in dialog.findChildren(QShortcut) if shortcut.key() == space] == []
    for pane in (dialog.table, dialog.player_pane, dialog.sentence_pane, dialog.definition_view):
        assert len(_shortcuts(pane, "P")) == 1, type(pane).__name__
    # Never on the player itself: a second P in the pane's focus chain is ambiguous (#120).
    assert _shortcuts(dialog.player_widget, "P") == []
    # Nor on the candidate list, for the same reason: it sits inside the Sentences pane.
    assert _shortcuts(dialog.sentence_list, "P") == []


def test_rebound_play_pause_fires_once_from_the_player_pane(qtbot, tmp_path) -> None:
    """Review Focus 2, behaviour: a real P on a player-pane button plays once, unambiguously."""
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    context = CurationMediaContext(video_file=video, subtitle_entries=list(ENTRIES), audio_padding=0.3)
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
        dialog = WordCurationDialog(
            [_word("食べる", "食べるのテスト", 5.0)],
            media_context=context,
            key_bindings={"curator.play_pause": "P"},
        )
    qtbot.addWidget(dialog)
    player = MagicMock()
    dialog.player_widget = player
    ambiguous: list[str] = []
    for shortcut in dialog.findChildren(QShortcut):
        shortcut.activatedAmbiguously.connect(partial(ambiguous.append, shortcut.key().toString(PORTABLE)))
    dialog.table.setCurrentCell(0, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()
    assert dialog.expand_next_button.isEnabled()
    _activate(qtbot, dialog, dialog.expand_next_button)

    QTest.keyClick(dialog.expand_next_button, Qt.Key.Key_P)

    assert player.toggle_play_pause.call_count == 1
    assert dialog._line_expansions == {}  # the key did not fall through to the button
    assert ambiguous == []

    dialog.table.setFocus()
    qtbot.waitUntil(dialog.table.hasFocus, timeout=1000)
    QTest.keyClick(dialog.table, Qt.Key.Key_Space)
    assert player.toggle_play_pause.call_count == 1  # Space no longer plays
    dialog.hide()


def test_the_table_answers_to_the_shipped_keys(qtbot, make_tokenized_words) -> None:
    """Defaults: no next/previous key, F2 edits, D (not K) marks known. Ctrl+C is install_copy_rows."""
    dialog = _curator(qtbot, make_tokenized_words(3))
    keys = {
        shortcut.key().toString(PORTABLE)
        for shortcut in dialog.table.findChildren(QShortcut, options=Qt.FindChildOption.FindDirectChildrenOnly)
    }
    assert keys == {"Space", "Shift+Space", ",", ".", "I", "O", "S", "D", "Ctrl+A", "Ctrl+D", "F2", "Ctrl+C"}


def test_an_unbound_action_installs_nothing(qtbot, make_tokenized_words) -> None:
    dialog = _curator(qtbot, make_tokenized_words(3), {"curator.toggle_include": ""})
    assert _table_shortcut(dialog, "S") is None


def test_a_rebound_edit_key_moves_off_f2(qtbot, make_tokenized_words) -> None:
    dialog = _curator(qtbot, make_tokenized_words(3), {"curator.edit_sentence": "F3"})
    assert _table_shortcut(dialog, "F2") is None
    assert _table_shortcut(dialog, "F3") is not None


def test_next_and_previous_word_move_like_the_arrows(qtbot, make_tokenized_words) -> None:
    dialog = _curator(qtbot, make_tokenized_words(3), {"curator.next_word": "J", "curator.previous_word": "K"})
    next_word, previous_word = _table_shortcut(dialog, "J"), _table_shortcut(dialog, "K")
    assert next_word is not None and previous_word is not None
    dialog.table.setCurrentCell(0, 1)

    next_word.activated.emit()
    assert (dialog.table.currentRow(), _highlighted(dialog)) == (1, [1])
    previous_word.activated.emit()
    assert (dialog.table.currentRow(), _highlighted(dialog)) == (0, [0])
    previous_word.activated.emit()  # the first row stays put, like Up
    assert dialog.table.currentRow() == 0


def test_next_word_skips_rows_the_search_hides(qtbot, make_tokenized_words) -> None:
    dialog = _curator(qtbot, make_tokenized_words(3), {"curator.next_word": "J"})
    dialog.table.setCurrentCell(0, 1)
    dialog.table.setRowHidden(1, True)
    shortcut = _table_shortcut(dialog, "J")
    assert shortcut is not None
    shortcut.activated.emit()
    assert dialog.table.currentRow() == 2


def test_a_modified_next_word_key_moves_the_highlight_instead_of_adding_to_it(qtbot, make_tokenized_words) -> None:
    """setCurrentCell reads the held modifiers: with Ctrl down it would toggle row 1 INTO the highlight."""
    dialog = _curator(qtbot, make_tokenized_words(3), {"curator.next_word": "Ctrl+J"})
    dialog.table.setCurrentCell(0, 1)
    _activate(qtbot, dialog, dialog.table)

    QTest.keyClick(dialog.table, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)

    assert dialog.table.currentRow() == 1
    assert _highlighted(dialog) == [1]
    dialog.hide()


# ---------------------------------------------------------------------------
# The player keys (play just this clip, frame steps) and the pane-button keys
# ---------------------------------------------------------------------------

#: Every key _install_pane_keys puts on each pane, at the shipped bindings.
PLAYER_KEYS = ("Space", "Shift+Space", ",", ".", "I", "O")

PLAYER_MODULE = "anki_miner.gui.widgets.subtitle_player_widget"


def test_every_player_key_sits_once_on_every_pane(qtbot, tmp_path) -> None:
    """One install helper, so each player key lands exactly where play/pause does (#120)."""
    dialog = _four_pane_curator(qtbot, tmp_path, None)

    for text in PLAYER_KEYS:
        for pane in (dialog.table, dialog.player_pane, dialog.sentence_pane, dialog.definition_view):
            assert len(_shortcuts(pane, text)) == 1, (text, type(pane).__name__)
        assert _shortcuts(dialog.player_widget, text) == [], text
        assert _shortcuts(dialog.sentence_list, text) == [], text
        key = QKeySequence.fromString(text, PORTABLE)
        assert len([sc for sc in dialog.findChildren(QShortcut) if sc.key() == key]) == 4, text


def test_the_player_keys_stay_off_the_hint_line(qtbot, tmp_path) -> None:
    """D48-B: the hint line names the essentials only."""
    text = _four_pane_curator(qtbot, tmp_path, None).key_hint_label.text()
    assert "Shift+Space" not in text
    assert "clip" not in text
    assert "frame" not in text


@pytest.fixture()
def fake_mpv():
    """Fake the mpv core at the player module's seam (as test_word_curation_frame_pick_live does).

    Active during WordCurationDialog.__init__, where the REAL player widget is
    built and pointed at its source.
    """
    from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget

    player = MagicMock(name="mpv.MPV")
    player.pause = True
    player.track_list = []
    player.time_pos = None
    player.event_callback.return_value = lambda fn: fn
    with (
        patch.object(MpvVideoWidget, "has_render_context", property(lambda self: True)),
        patch(f"{PLAYER_MODULE}.mpv_available", return_value=True),
        patch(f"{PLAYER_MODULE}.create_mpv_player", return_value=player),
    ):
        yield player


def _live_curator(qtbot, tmp_path: Path, key_bindings=None) -> WordCurationDialog:
    """A real player over the fake core, the word focused and its file loaded."""
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    context = CurationMediaContext(video_file=video, subtitle_entries=list(ENTRIES), audio_padding=0.3)
    dialog = WordCurationDialog(
        [_word("食べる", "食べるのテスト", 5.0)],
        media_context=context,
        commit_known_callback=lambda forms: len(forms),
        key_bindings=key_bindings,
    )
    qtbot.addWidget(dialog)
    dialog.player_widget._on_file_loaded()
    dialog.player_widget._on_duration(60.0)
    dialog.table.setCurrentCell(0, 1)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()
    return dialog


def test_shift_space_plays_just_the_clip_and_stops_it(qtbot, tmp_path, fake_mpv) -> None:
    dialog = _live_curator(qtbot, tmp_path)
    _activate(qtbot, dialog, dialog.table)
    fake_mpv.command.reset_mock()

    QTest.keyClick(dialog.table, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)

    # The clip window (the line padded by 0.3 s), not the bare line start Space plays from.
    fake_mpv.command.assert_called_once_with("seek", 4.7, "absolute+exact")
    assert fake_mpv.pause is False
    assert dialog.clip_editor._playing
    assert dialog.player_widget._range_end == pytest.approx(7.3)

    QTest.keyClick(dialog.table, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)

    assert fake_mpv.pause is True
    assert not dialog.clip_editor._playing
    dialog.hide()


def test_comma_and_period_step_one_frame(qtbot, tmp_path, fake_mpv) -> None:
    dialog = _live_curator(qtbot, tmp_path)
    _activate(qtbot, dialog, dialog.table)
    fake_mpv.command.reset_mock()

    QTest.keyClick(dialog.table, Qt.Key.Key_Comma)
    QTest.keyClick(dialog.table, Qt.Key.Key_Period)

    assert fake_mpv.command.call_args_list == [(("frame-back-step",),), (("frame-step",),)]
    dialog.hide()


def test_a_frame_key_fires_once_from_a_focused_player_pane_button(qtbot, tmp_path, fake_mpv) -> None:
    """The steppers' keys hang off the pane like play/pause: never ambiguous, never eaten by a button."""
    dialog = _live_curator(qtbot, tmp_path)
    ambiguous: list[str] = []
    for shortcut in dialog.findChildren(QShortcut):
        shortcut.activatedAmbiguously.connect(partial(ambiguous.append, shortcut.key().toString(PORTABLE)))
    assert dialog.expand_next_button.isEnabled()
    _activate(qtbot, dialog, dialog.expand_next_button)
    fake_mpv.command.reset_mock()

    QTest.keyClick(dialog.expand_next_button, Qt.Key.Key_Period)
    QTest.keyClick(dialog.expand_next_button, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)

    assert fake_mpv.command.call_args_list[0] == (("frame-step",),)
    assert dialog.clip_editor._playing
    assert dialog._line_expansions == {}  # Shift+Space did not fall through to the button
    assert ambiguous == []
    dialog.hide()


def test_i_and_o_put_the_clip_ends_at_the_playhead(qtbot, tmp_path, fake_mpv) -> None:
    """I from the table, O from a focused player-pane button: each edge lands on mpv's position."""
    dialog = _live_curator(qtbot, tmp_path)
    ambiguous: list[str] = []
    for shortcut in dialog.findChildren(QShortcut):
        shortcut.activatedAmbiguously.connect(partial(ambiguous.append, shortcut.key().toString(PORTABLE)))
    _activate(qtbot, dialog, dialog.table)
    fake_mpv.pause = False  # playing: the press must not stop it
    fake_mpv.time_pos = 4.2

    QTest.keyClick(dialog.table, Qt.Key.Key_I)

    assert dialog._clip_overrides == {0: (4.2, 7.3)}
    dialog.expand_next_button.setFocus()
    qtbot.waitUntil(dialog.expand_next_button.hasFocus, timeout=1000)
    fake_mpv.time_pos = 6.5

    QTest.keyClick(dialog.expand_next_button, Qt.Key.Key_O)

    assert dialog._clip_overrides == {0: (4.2, 6.5)}
    assert dialog.clip_editor.current_window() == (4.2, 6.5)
    assert fake_mpv.pause is False
    assert dialog._line_expansions == {}
    assert ambiguous == []
    dialog.hide()


def test_the_playhead_keys_wait_for_the_chosen_episode(qtbot, tmp_path, fake_mpv) -> None:
    dialog = _live_curator(qtbot, tmp_path)
    fake_mpv.time_pos = 4.2
    with patch.object(WordCurationDialog, "_chosen_episode_displayed", return_value=False):
        _table_shortcut(dialog, "I").activated.emit()
        _table_shortcut(dialog, "O").activated.emit()
    assert dialog._clip_overrides == {}


def test_frame_keys_wait_for_the_chosen_episode(qtbot, tmp_path, fake_mpv) -> None:
    """Season curation: while the source swap is in flight the frame on screen is another episode's."""
    dialog = _live_curator(qtbot, tmp_path)
    fake_mpv.command.reset_mock()
    with patch.object(WordCurationDialog, "_chosen_episode_displayed", return_value=False):
        dialog._step_frame(1)
    fake_mpv.command.assert_not_called()


def test_a_saved_key_on_comma_wins_over_the_new_frame_default(qtbot, tmp_path, fake_mpv) -> None:
    """A config that had already put Mark known on "," keeps it; Step one frame back ships unbound."""
    dialog = _live_curator(qtbot, tmp_path, {"curator.mark_known": ","})
    comma = QKeySequence.fromString(",", PORTABLE)
    assert len([sc for sc in dialog.findChildren(QShortcut) if sc.key() == comma]) == 1
    assert _shortcuts(dialog.player_pane, ",") == []
    form = dialog.table.item(0, 1).text()
    _activate(qtbot, dialog, dialog.table)
    fake_mpv.command.reset_mock()

    QTest.keyClick(dialog.table, Qt.Key.Key_Comma)

    assert dialog.pending_known_forms() == {form}
    fake_mpv.command.assert_not_called()
    dialog.hide()


def test_the_frame_pick_keys_click_their_buttons(qtbot, tmp_path, fake_mpv) -> None:
    dialog = _live_curator(qtbot, tmp_path, {"curator.use_frame": "U", "curator.reset_frame": "Shift+U"})
    use_frame, reset_frame = _table_shortcut(dialog, "U"), _table_shortcut(dialog, "Shift+U")
    assert use_frame is not None and reset_frame is not None
    fake_mpv.time_pos = 6.4

    reset_frame.activated.emit()  # hidden, so disabled: nothing to reset
    assert dialog._screenshot_overrides == {}
    use_frame.activated.emit()
    assert dialog._screenshot_overrides == {0: pytest.approx(6.4)}
    reset_frame.activated.emit()
    assert dialog._screenshot_overrides == {}


def test_the_use_frame_key_waits_for_a_focused_word(qtbot, tmp_path, fake_mpv) -> None:
    """The button is disabled with no word focused, and the key is that button."""
    dialog = _live_curator(qtbot, tmp_path, {"curator.use_frame": "U"})
    dialog.use_frame_button.setEnabled(False)
    shortcut = _table_shortcut(dialog, "U")
    assert shortcut is not None

    shortcut.activated.emit()

    assert dialog._screenshot_overrides == {}


def test_the_reset_clip_key_restores_the_default_window(qtbot, tmp_path, fake_mpv) -> None:
    dialog = _live_curator(qtbot, tmp_path, {"curator.reset_clip": "R"})
    dialog.clip_editor.slider.values_changed.emit(40, 80)  # the user drags both ends
    assert 0 in dialog._clip_overrides
    shortcut = _table_shortcut(dialog, "R")
    assert shortcut is not None

    shortcut.activated.emit()

    assert dialog._clip_overrides == {}
    assert dialog.clip_editor.current_window() == (4.7, 7.3)


def _stub_player_curator(qtbot, tmp_path: Path, key_bindings) -> WordCurationDialog:
    """The line-expansion layout of test_rebound_play_pause_fires_once_from_the_player_pane."""
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    context = CurationMediaContext(video_file=video, subtitle_entries=list(ENTRIES), audio_padding=0.3)
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
        dialog = WordCurationDialog(
            [_word("食べる", "食べるのテスト", 5.0)], media_context=context, key_bindings=key_bindings
        )
    qtbot.addWidget(dialog)
    dialog.player_widget = MagicMock()
    return dialog


def test_the_line_keys_click_their_buttons(qtbot, tmp_path) -> None:
    dialog = _stub_player_curator(
        qtbot,
        tmp_path,
        {"curator.expand_previous": "Q", "curator.expand_next": "E", "curator.reset_lines": "W"},
    )
    previous, following, reset = (_table_shortcut(dialog, key) for key in ("Q", "E", "W"))
    assert previous is not None and following is not None and reset is not None

    following.activated.emit()  # no word focused yet: the buttons are disabled
    assert dialog._line_expansions == {}

    dialog.table.setCurrentCell(0, 1)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()
    following.activated.emit()
    assert dialog._line_expansions == {0: (0, 1)}
    previous.activated.emit()
    assert dialog._line_expansions == {0: (1, 1)}
    previous.activated.emit()  # no earlier line: the button disabled itself
    assert dialog._line_expansions == {0: (1, 1)}
    reset.activated.emit()
    assert dialog._line_expansions == {}


def test_the_sentence_keys_pick_like_a_click(qtbot, tmp_path) -> None:
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    first = _word("食べる", "朝ごはんを食べる", 1.0)
    first.sentence_candidates = [_word("食べる", "朝ごはんを食べる", 1.0), _word("食べる", "パンを食べる", 5.0)]
    context = CurationMediaContext(video_file=video, subtitle_entries=[(1.0, 3.0, "朝ごはんを食べる")])
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
        dialog = WordCurationDialog(
            [first, _word("走る", "走るのテスト", 9.0)],
            media_context=context,
            key_bindings={"curator.previous_sentence": "Q", "curator.next_sentence": "E"},
        )
    qtbot.addWidget(dialog)
    dialog.player_widget = MagicMock()
    previous, following = _table_shortcut(dialog, "Q"), _table_shortcut(dialog, "E")
    assert previous is not None and following is not None
    dialog.table.setCurrentCell(0, 1)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()
    assert dialog.sentence_list.currentRow() == 0

    following.activated.emit()
    assert dialog.sentence_list.currentRow() == 1
    assert dialog._chosen[0].sentence == "パンを食べる"  # _on_candidate_chosen ran
    following.activated.emit()  # the last row stays put
    assert dialog.sentence_list.currentRow() == 1
    previous.activated.emit()
    assert dialog._chosen[0].sentence == "朝ごはんを食べる"

    dialog.table.setCurrentCell(1, 1)  # one sentence only: nothing to pick from
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()
    following.activated.emit()
    assert 1 not in dialog._chosen


#: Every new action, bound, for runs that lack the controls.
_ALL_NEW = {
    "curator.expand_previous": "Q",
    "curator.expand_next": "E",
    "curator.reset_lines": "W",
    "curator.use_frame": "U",
    "curator.reset_frame": "Shift+U",
    "curator.reset_clip": "R",
    "curator.previous_sentence": "Z",
    "curator.next_sentence": "X",
}


def test_a_run_without_the_controls_gives_their_keys_nothing_to_do(qtbot, make_tokenized_words) -> None:
    """Table-only (manga, reading, no player): the keys exist or not, and none raises."""
    dialog = _curator(qtbot, make_tokenized_words(3), _ALL_NEW)
    dialog.table.setCurrentCell(0, 1)
    for text in ("Q", "E", "W", "U", "Shift+U", "R"):
        assert _table_shortcut(dialog, text) is None, text  # no button, no key
    states = [dialog.table.item(row, 0).checkState() for row in range(3)]

    for text in ("Z", "X", *PLAYER_KEYS):
        shortcut = _table_shortcut(dialog, text)
        assert shortcut is not None, text
        shortcut.activated.emit()

    assert [dialog.table.item(row, 0).checkState() for row in range(3)] == states
    assert dialog.table.currentRow() == 0

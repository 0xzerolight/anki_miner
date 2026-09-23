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
    for key in ("S", "D", "J", "K"):
        shortcut = _table_shortcut(dialog, key)
        assert shortcut is not None, key  # non-vacuous: each key IS bound, on the table
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    dialog.table.setCurrentCell(1, 1)
    states = [dialog.table.item(row, 0).checkState() for row in range(3)]
    _activate(qtbot, dialog, dialog.search_input)

    QTest.keyClicks(dialog.search_input, "sdjk")

    assert dialog.search_input.text() == "sdjk"
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
    for pane in (dialog.table, dialog.player_pane, dialog.sentence_list, dialog.definition_view):
        assert len(_shortcuts(pane, "P")) == 1, type(pane).__name__
    # Never on the player itself: a second P in the pane's focus chain is ambiguous (#120).
    assert _shortcuts(dialog.player_widget, "P") == []


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
    assert keys == {"Space", "S", "D", "Ctrl+A", "Ctrl+D", "F2", "Ctrl+C"}


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

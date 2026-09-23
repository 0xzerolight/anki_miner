"""Settings -> Keyboard: one row per action, validated inline, committed at once."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.utils.key_bindings import KEY_ACTIONS
from anki_miner.gui.widgets.panels import KeyboardSettingsPanel

PORTABLE = QKeySequence.SequenceFormat.PortableText


@pytest.fixture
def panel(qtbot):
    widget = KeyboardSettingsPanel()
    qtbot.addWidget(widget)
    return widget


def _shown(panel: KeyboardSettingsPanel, action_id: str) -> str:
    return panel._editors[action_id].keySequence().toString(PORTABLE)


def test_one_row_per_action_showing_the_shipped_key(panel):
    assert set(panel._editors) == {action.id for action in KEY_ACTIONS}
    for action in KEY_ACTIONS:
        assert _shown(panel, action.id) == action.default


def test_each_editor_records_one_chord(panel):
    assert all(editor.maximumSequenceLength() == 1 for editor in panel._editors.values())


def test_an_accepted_key_emits_the_overrides_only(panel, qtbot):
    with qtbot.waitSignal(panel.key_bindings_changed, timeout=1000) as blocker:
        assert panel.set_binding("curator.mark_known", QKeySequence("J"))
    assert blocker.args == [{"curator.mark_known": "J"}]
    assert _shown(panel, "curator.mark_known") == "J"


def test_a_duplicate_is_refused_and_names_the_holder(panel, qtbot):
    with qtbot.assertNotEmitted(panel.key_bindings_changed):
        assert not panel.set_binding("curator.mark_known", QKeySequence("S"))
    error = panel._errors["curator.mark_known"]
    assert not error.isHidden()
    assert "Include or exclude the highlighted words" in error.text()
    assert _shown(panel, "curator.mark_known") == "D"  # the editor snaps back


def test_a_reserved_curator_key_is_refused(panel, qtbot):
    with qtbot.assertNotEmitted(panel.key_bindings_changed):
        assert not panel.set_binding("curator.next_word", QKeySequence("Down"))
    assert not panel._errors["curator.next_word"].isHidden()
    assert _shown(panel, "curator.next_word") == ""


def test_a_bare_app_key_is_refused(panel, qtbot):
    with qtbot.assertNotEmitted(panel.key_bindings_changed):
        assert not panel.set_binding("app.open_settings", QKeySequence("D"))
    assert "Ctrl" in panel._errors["app.open_settings"].text()


def test_a_later_accepted_key_clears_the_error(panel):
    panel.set_binding("curator.mark_known", QKeySequence("S"))
    assert panel.set_binding("curator.mark_known", QKeySequence("J"))
    assert panel._errors["curator.mark_known"].isHidden()


def test_clear_unbinds(panel, qtbot):
    with qtbot.waitSignal(panel.key_bindings_changed, timeout=1000) as blocker:
        panel._clear_buttons["curator.play_pause"].click()
    assert blocker.args == [{"curator.play_pause": ""}]
    assert _shown(panel, "curator.play_pause") == ""


def test_reset_returns_one_row_to_its_default(panel, qtbot):
    panel.set_binding("curator.mark_known", QKeySequence("J"))
    panel.set_binding("curator.play_pause", QKeySequence("P"))
    with qtbot.waitSignal(panel.key_bindings_changed, timeout=1000) as blocker:
        panel._reset_buttons["curator.mark_known"].click()
    assert blocker.args == [{"curator.play_pause": "P"}]
    assert _shown(panel, "curator.mark_known") == "D"


def test_restore_defaults_resets_every_row_in_one_commit(panel, qtbot):
    panel.set_binding("curator.mark_known", QKeySequence("J"))
    panel.set_binding("app.open_settings", QKeySequence("Ctrl+Shift+S"))
    with qtbot.waitSignal(panel.key_bindings_changed, timeout=1000) as blocker:
        panel.restore_defaults_button.click()
    assert blocker.args == [{}]
    assert _shown(panel, "curator.mark_known") == "D"
    assert _shown(panel, "app.open_settings") == "Ctrl+,"


def test_load_from_config_repaints_without_emitting(panel, qtbot, test_config):
    with qtbot.assertNotEmitted(panel.key_bindings_changed):
        panel.load_from_config(replace(test_config, key_bindings={"curator.mark_known": "J"}))
    assert _shown(panel, "curator.mark_known") == "J"


def test_a_typed_key_commits_through_the_editor(panel, qtbot):
    """The real path: focus a row's editor and press one key."""
    editor = panel._editors["curator.next_word"]
    panel.show()
    qtbot.waitExposed(panel)
    QApplication.setActiveWindow(panel)
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus, timeout=1000)
    with qtbot.waitSignal(panel.key_bindings_changed, timeout=1000) as blocker:
        QTest.keyClick(editor, Qt.Key.Key_J)
    assert blocker.args == [{"curator.next_word": "J"}]
    panel.hide()


def test_a_lone_modifier_press_keeps_the_binding(panel, qtbot):
    """A modifier pressed and released on its own is not "unbind" (amendment A)."""
    editor = panel._editors["curator.mark_known"]
    panel.show()
    qtbot.waitExposed(panel)
    QApplication.setActiveWindow(panel)
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus, timeout=1000)
    with qtbot.assertNotEmitted(panel.key_bindings_changed), qtbot.waitSignal(editor.editingFinished, timeout=3000):
        QTest.keyPress(editor, Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyRelease(editor, Qt.Key.Key_Shift, Qt.KeyboardModifier.NoModifier)
    assert _shown(panel, "curator.mark_known") == "D"
    panel.hide()


def test_esc_cancels_a_recording_and_keeps_the_binding(panel, qtbot):
    """Esc is a finishing key that cancels rather than being recorded (amendment A)."""
    editor = panel._editors["curator.mark_known"]
    panel.show()
    qtbot.waitExposed(panel)
    QApplication.setActiveWindow(panel)
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus, timeout=1000)
    with qtbot.assertNotEmitted(panel.key_bindings_changed):
        QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert _shown(panel, "curator.mark_known") == "D"
    # Esc is also refused as a reserved curator key (it is Esc in _reserved), so
    # without the finishing-key fix it would reach validation and light up the
    # error banner; the fix cancels the recording before validation ever runs.
    assert panel._errors["curator.mark_known"].isHidden()
    panel.hide()


def test_the_page_says_an_open_curator_keeps_its_keys(panel):
    assert "already open" in panel.helper_label.text()

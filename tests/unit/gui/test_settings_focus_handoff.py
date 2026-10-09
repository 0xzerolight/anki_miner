"""A Settings control that hides itself once pressed hands focus to its own row (B3.5c).

Keyboard Reset, Dictionaries "Reset to default" and the last Excluded Decks
Remove are offered only while they would change something, so pressing one
hides it. Hiding the focus widget makes Qt focus the next control in the tab
chain, which threw a keyboard user into another row or section. Each now passes
focus to the control it acted on, and a keyboard press keeps its ring there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QWidget

from anki_miner.gui.utils.focus_ring import (
    KEYBOARD_FOCUS_PROPERTY,
    install_keyboard_focus_ring,
    remove_keyboard_focus_ring,
)
from anki_miner.gui.widgets.panels import KeyboardSettingsPanel
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


@pytest.fixture(autouse=True)
def _keyboard_focus_ring(qapp):
    """Run under the production focus filter, which is what sets the mark read below."""
    install_keyboard_focus_ring(qapp)
    yield
    remove_keyboard_focus_ring(qapp)


def _show(qtbot, panel: QWidget) -> None:
    panel.show()
    qtbot.waitExposed(panel)


def _focus(widget: QWidget, reason: Qt.FocusReason) -> None:
    widget.clearFocus()
    QApplication.processEvents()
    widget.setFocus(reason)
    QApplication.processEvents()
    assert widget.hasFocus()


def test_keyboard_reset_hands_focus_to_its_row_editor(qtbot):
    panel = KeyboardSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_binding("curator.mark_known", QKeySequence("J"))
    _show(qtbot, panel)
    reset = panel._reset_buttons["curator.mark_known"]
    _focus(reset, Qt.FocusReason.TabFocusReason)

    reset.click()
    QApplication.processEvents()

    editor = panel._editors["curator.mark_known"]
    assert reset.isHidden()
    assert QApplication.focusWidget() is editor
    assert editor.property(KEYBOARD_FOCUS_PROPERTY)


def test_a_clicked_reset_hands_focus_on_without_a_ring(qtbot):
    panel = KeyboardSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_binding("curator.mark_known", QKeySequence("J"))
    _show(qtbot, panel)
    reset = panel._reset_buttons["curator.mark_known"]
    _focus(reset, Qt.FocusReason.MouseFocusReason)

    reset.click()
    QApplication.processEvents()

    editor = panel._editors["curator.mark_known"]
    assert QApplication.focusWidget() is editor
    assert not editor.property(KEYBOARD_FOCUS_PROPERTY)


def test_dictionaries_reset_hands_focus_to_the_folder_field(qtbot, tmp_path: Path):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    # The first show starts a disk scan; the storage row is what is under test.
    panel._scanned = True
    _show(qtbot, panel)
    reset = panel._reset_dicts_root_btn
    assert not reset.isHidden()
    _focus(reset, Qt.FocusReason.TabFocusReason)

    reset.click()
    QApplication.processEvents()

    assert reset.isHidden()
    assert QApplication.focusWidget() is panel.dicts_root_selector.input
    assert panel.dicts_root_selector.input.property(KEYBOARD_FOCUS_PROPERTY)


def test_removing_the_last_excluded_deck_hands_focus_to_add(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("Kanji",))
    _show(qtbot, panel)
    panel.excluded_decks_list.setCurrentRow(0)
    remove = panel.remove_deck_button
    _focus(remove, Qt.FocusReason.TabFocusReason)

    remove.click()
    QApplication.processEvents()

    assert remove.isHidden()
    assert QApplication.focusWidget() is panel.add_deck_button
    assert panel.add_deck_button.property(KEYBOARD_FOCUS_PROPERTY)

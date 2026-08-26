"""A chain panel rebuild never throws keyboard focus out of the panel.

Every chain settings panel repopulates its list by destroying every row widget
(``_rebuild_list``) or by disabling the reorder controls and clearing the list
(``_show_loading_placeholder``). Qt answers both by calling ``focusNextChild()``,
which WRAPS the window's tab order — and the header sits first in that order, so
focus left the panel entirely and landed on the theme selector in the top-right
corner. Measured offscreen against the real window, a click on a row's Enabled
checkbox ended with ``QApplication.focusWidget() is header.theme_combo``.

The user-visible half of that bug was the accent border it lit on the theme
combo (``common.qss`` gives non-editable inputs a ``:focus`` border, ungated by
``keyboardFocus`` — and the wrap-around arrives with ``TabFocusReason``, so the
keyboard gate would not have caught it either). The stylesheet now keeps that
corner quiet; this file defends the other half — focus staying where the user is
working.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QVBoxLayout, QWidget

from anki_miner.config import AudioSourceEntry, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.gui.utils.focus_ring import (
    KEYBOARD_FOCUS_PROPERTY,
    install_keyboard_focus_ring,
    remove_keyboard_focus_ring,
)
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel

#: Two entries, so a rebuild has a row to put focus back on and the index the
#: focused row had is not trivially 0-of-1.
CHAIN = (
    AudioSourceEntry(kind="jpod101", enabled=True),
    AudioSourceEntry(kind="googletts", enabled=False),
)


@pytest.fixture(autouse=True)
def _keyboard_focus_ring(qapp):
    """Run under the production focus filter.

    The ``keyboardFocus`` mark these tests read is only ever set by that filter,
    so asserting on it without installing it passes with the fix deleted.
    Removed on teardown: ``qapp`` is shared for the whole pytest worker, and an
    application-level filter left behind marks widgets in every later file.
    """
    install_keyboard_focus_ring(qapp)
    yield
    remove_keyboard_focus_ring(qapp)


def _suppress_first_show_scan(panel) -> None:
    """Stop the lazy first-show registry scan from clearing the rows.

    Showing a chain panel runs a disk scan off-thread and renders the loading
    placeholder while it is in flight, so a shown panel has no rows to focus.
    The scan is not what is under test here — the rebuild is.
    """
    panel._scanned = True


@pytest.fixture
def host(qtbot, tmp_path: Path):
    """An audio chain panel with a decoy combo *after* it in the tab order.

    The decoy stands in for the application's header: something focusable that
    the wrap-around can land on. Without it the panel is the whole window and
    ``focusNextChild()`` has nowhere else to go, which makes the test pass
    against the unfixed code.
    """
    container = QWidget()
    qtbot.addWidget(container)
    layout = QVBoxLayout(container)

    panel = AudioPackSettingsPanel(tmp_path)
    _suppress_first_show_scan(panel)
    layout.addWidget(panel)
    decoy = QComboBox()
    decoy.setObjectName("decoy")
    decoy.addItems(["one", "two"])
    layout.addWidget(decoy)

    container.show()
    qtbot.waitExposed(container)
    # After show: the rows have to exist in a *shown* panel for focus to move
    # at all, and the first-show scan would replace them with the placeholder.
    panel.set_chain(CHAIN)
    return container, panel, decoy


def _focus_first_row_toggle(panel) -> None:
    """Focus row 0's Enabled checkbox the way a mouse click would.

    ``clearFocus()`` first: ``setFocus`` on a widget that already holds focus
    sends no ``QFocusEvent`` at all, and ``show()`` hands focus to the first
    focusable child.
    """
    row = panel._row_widget(0)
    assert row is not None, "the panel rendered no rows to focus"
    row.checkbox.clearFocus()
    QApplication.processEvents()
    row.checkbox.setFocus(Qt.FocusReason.MouseFocusReason)
    QApplication.processEvents()
    assert row.checkbox.hasFocus()


class TestFocusStaysInThePanel:
    def test_a_rebuild_puts_focus_back_on_the_same_row(self, host):
        """Not just "somewhere in the panel" — the control the user was on.

        "Inside the panel" is too weak an assertion to be a test: with the guard
        removed Qt parks focus on the list often enough that it passes anyway.
        Which control ends up focused is the part that only the guard decides.
        """
        _container, panel, _decoy = host
        _focus_first_row_toggle(panel)

        panel.set_chain(CHAIN)
        QApplication.processEvents()

        row = panel._row_widget(0)
        assert row is not None
        assert QApplication.focusWidget() is row.checkbox

    def test_the_restored_focus_wears_no_keyboard_ring(self, host):
        """Restoring uses ``OtherFocusReason``, which the ring filter ignores.

        A restore spelled ``TabFocusReason`` would mark the widget and paint the
        keyboard ring on a panel the user reached with the mouse — the very
        thing ``focus_ring.py`` exists to prevent.
        """
        _container, panel, _decoy = host
        _focus_first_row_toggle(panel)

        panel.set_chain(CHAIN)
        QApplication.processEvents()

        focused = QApplication.focusWidget()
        assert focused is not None
        assert not focused.property(KEYBOARD_FOCUS_PROPERTY)

    def test_the_loading_placeholder_keeps_focus_too(self, host):
        """This path disables the reorder controls as well as clearing the list.

        With no rows left to return to, the list itself is the target — and it
        must arrive there unmarked. Qt's own fallback focuses with
        ``TabFocusReason``, which rings the list in the accent; that is the same
        bug one widget along.
        """
        _container, panel, decoy = host
        _focus_first_row_toggle(panel)

        panel._show_loading_placeholder()
        QApplication.processEvents()

        focused = QApplication.focusWidget()
        assert focused is not decoy
        assert focused is panel._list
        assert focused is not None
        assert not focused.property(KEYBOARD_FOCUS_PROPERTY)

    def test_focus_outside_the_panel_is_left_alone(self, host):
        """The guard restores; it does not steal.

        A rebuild triggered while the user is typing somewhere else (the auto-save
        round trip fires one on every settings edit) must not yank focus into a
        list they are not looking at.
        """
        _container, panel, decoy = host
        decoy.setFocus(Qt.FocusReason.MouseFocusReason)
        QApplication.processEvents()

        panel.set_chain(CHAIN)
        QApplication.processEvents()

        assert QApplication.focusWidget() is decoy


class TestFocusFollowsADisabledArrow:
    """The other half: a *move* disables the very control that ordered it.

    Pressing ``↓`` until the row reaches the bottom greys that ``↓`` out, which
    is the same focus-wrap trap one widget along — except here there is a
    better answer than the row's toggle. The arrow pointing back the way the
    row came is still enabled and undoes the move, so the press lands there.
    Landing on the toggle instead would hand the next Space press to *switch
    this source off*, which is not what the user was doing.
    """

    def _focus_first_row_down_arrow(self, panel, reason: Qt.FocusReason):
        row = panel._row_widget(0)
        assert row is not None, "the panel rendered no rows to focus"
        row.down_button.clearFocus()
        QApplication.processEvents()
        row.down_button.setFocus(reason)
        QApplication.processEvents()
        assert row.down_button.hasFocus()
        return row

    def test_the_press_lands_on_the_rows_other_arrow(self, host):
        _container, panel, decoy = host
        row = self._focus_first_row_down_arrow(panel, Qt.FocusReason.MouseFocusReason)

        row.down_button.click()
        QApplication.processEvents()

        assert panel._row_widget(1) is row, "the row did not reach the end of the list"
        assert not row.down_button.isEnabled()
        assert QApplication.focusWidget() is not decoy
        assert QApplication.focusWidget() is row.up_button

    def test_the_landing_keeps_the_reason_the_press_had(self, host):
        """A mouse press must not light the keyboard ring, and Tab must keep it."""
        _container, panel, _decoy = host
        clicked = self._focus_first_row_down_arrow(panel, Qt.FocusReason.MouseFocusReason)
        clicked.down_button.click()
        QApplication.processEvents()

        assert not clicked.up_button.property(KEYBOARD_FOCUS_PROPERTY)

        panel.set_chain(CHAIN)
        tabbed = self._focus_first_row_down_arrow(panel, Qt.FocusReason.TabFocusReason)
        tabbed.down_button.click()
        QApplication.processEvents()

        assert tabbed.up_button.property(KEYBOARD_FOCUS_PROPERTY)


@pytest.mark.parametrize(
    ("factory", "chain"),
    [
        (AudioPackSettingsPanel, CHAIN),
        (DictionarySettingsPanel, (ChainEntry(kind="jisho", dict_id=None, enabled=True),)),
        (FrequencySettingsPanel, (FreqEntry(source_id="jpdb", enabled=True),)),
        (PitchSettingsPanel, (PitchSourceEntry(source_id="legacy-pitch", enabled=True),)),
    ],
)
def test_every_chain_panel_inherits_the_guard(qtbot, tmp_path, factory, chain):
    """The fix lives on the shared base, so all four panels must be covered."""
    container = QWidget()
    qtbot.addWidget(container)
    layout = QVBoxLayout(container)
    panel = factory(tmp_path)
    _suppress_first_show_scan(panel)
    layout.addWidget(panel)
    decoy = QComboBox()
    layout.addWidget(decoy)
    container.show()
    qtbot.waitExposed(container)
    panel.set_chain(chain)

    _focus_first_row_toggle(panel)
    panel.set_chain(chain)
    QApplication.processEvents()

    row = panel._row_widget(0)
    assert row is not None
    assert QApplication.focusWidget() is row.checkbox

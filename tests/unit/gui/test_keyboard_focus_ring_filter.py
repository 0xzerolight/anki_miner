"""The filter that tells the stylesheet a keyboard put focus here.

``tests/unit/gui/test_focus_ring.py`` asserts the visible outcome in pixels.
This file asserts the mechanism underneath, where the interesting cases are the
ones a pixel test cannot stage: every ``Qt.FocusReason`` in turn, a window
losing activation, and the promise that clicking around the application does no
restyling at all.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import QApplication, QListWidget

from anki_miner.gui.utils.focus_ring import (
    KEYBOARD_FOCUS_PROPERTY,
    KeyboardFocusRingFilter,
    install_keyboard_focus_ring,
    remove_keyboard_focus_ring,
)


@pytest.fixture(autouse=True)
def _filter_installed(qapp):
    """Install for the test, remove after.

    ``qapp`` is shared for the whole pytest worker, so an application-level
    event filter left behind goes on marking widgets in every file that runs
    after this one.
    """
    install_keyboard_focus_ring(qapp)
    yield
    remove_keyboard_focus_ring(qapp)


@pytest.fixture
def marked_list(qtbot):
    """A list under the real filter, focused by nothing yet."""
    widget = QListWidget()
    widget.addItems(["one", "two"])
    qtbot.addWidget(widget)
    widget.show()
    return widget


def _focus_in(widget, reason: Qt.FocusReason) -> None:
    QApplication.sendEvent(widget, QFocusEvent(QEvent.Type.FocusIn, reason))


def _focus_out(widget, reason: Qt.FocusReason) -> None:
    QApplication.sendEvent(widget, QFocusEvent(QEvent.Type.FocusOut, reason))


KEYBOARD = [
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
    Qt.FocusReason.MenuBarFocusReason,
]

NOT_KEYBOARD = [
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.ActiveWindowFocusReason,
    Qt.FocusReason.PopupFocusReason,
    Qt.FocusReason.OtherFocusReason,
]


@pytest.mark.parametrize("reason", KEYBOARD, ids=lambda r: r.name)
def test_a_keystroke_marks_the_widget(marked_list, reason):
    """Shortcut and menu-bar count: an accelerator is a keystroke, and the ring
    is what tells the user where it landed."""
    _focus_in(marked_list, reason)

    assert marked_list.property(KEYBOARD_FOCUS_PROPERTY) is True


@pytest.mark.parametrize("reason", NOT_KEYBOARD, ids=lambda r: r.name)
def test_everything_else_leaves_the_widget_unmarked(marked_list, reason):
    """``MouseFocusReason`` is the bug this whole module exists for."""
    _focus_in(marked_list, reason)

    assert not marked_list.property(KEYBOARD_FOCUS_PROPERTY)


def test_leaving_clears_the_mark(marked_list):
    _focus_in(marked_list, Qt.FocusReason.TabFocusReason)
    _focus_out(marked_list, Qt.FocusReason.MouseFocusReason)

    assert marked_list.property(KEYBOARD_FOCUS_PROPERTY) is False


@pytest.mark.parametrize(
    "reason",
    [Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason],
    ids=lambda r: r.name,
)
def test_deactivating_or_popping_up_keeps_the_mark(marked_list, reason):
    """Alt-tab away, or open a combo popup, and the ring must still be there
    when you come back -- not gone until the next Tab.

    The round trip is what matters: Qt delivers the return ``FocusIn`` with the
    SAME reason, which is not a keyboard reason, so exempting the ``FocusOut``
    leg alone just moved the clearing one event later.
    """
    _focus_in(marked_list, Qt.FocusReason.TabFocusReason)
    _focus_out(marked_list, reason)
    assert marked_list.property(KEYBOARD_FOCUS_PROPERTY) is True

    _focus_in(marked_list, reason)
    assert marked_list.property(KEYBOARD_FOCUS_PROPERTY) is True


@pytest.mark.parametrize(
    "reason",
    [Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason],
    ids=lambda r: r.name,
)
def test_the_round_trip_does_not_mark_a_mouse_focused_widget(marked_list, reason):
    """The exemption preserves the mark, it does not grant one.

    A widget the user clicked into must come back from a deactivate or a popup
    exactly as unmarked as it went in.
    """
    _focus_in(marked_list, Qt.FocusReason.MouseFocusReason)
    _focus_out(marked_list, reason)
    _focus_in(marked_list, reason)

    assert not marked_list.property(KEYBOARD_FOCUS_PROPERTY)


def test_a_click_never_repolishes(marked_list, monkeypatch):
    """The promise that keeps mouse navigation free.

    An unmarked widget clicked into is already in the right state, so the filter
    returns after one property read. Without the early return every click in the
    application would re-run a widget's whole style computation for nothing.
    """
    style = marked_list.style()
    assert style is not None
    unpolished: list[object] = []
    monkeypatch.setattr(style, "unpolish", lambda widget: unpolished.append(widget))

    _focus_in(marked_list, Qt.FocusReason.MouseFocusReason)
    _focus_out(marked_list, Qt.FocusReason.MouseFocusReason)

    assert unpolished == [], "a mouse click restyled the widget it focused"


def test_a_keystroke_does_repolish(marked_list, monkeypatch):
    """The other half: a property alone changes nothing until Qt re-reads it."""
    style = marked_list.style()
    assert style is not None
    unpolished: list[object] = []
    monkeypatch.setattr(style, "unpolish", lambda widget: unpolished.append(widget))

    _focus_in(marked_list, Qt.FocusReason.TabFocusReason)

    assert unpolished == [marked_list]


def test_installing_twice_leaves_one_filter(qapp):
    """Two filters would do the same work twice on every focus change."""
    first = install_keyboard_focus_ring(qapp)
    second = install_keyboard_focus_ring(qapp)

    assert first is second
    assert len(qapp.findChildren(KeyboardFocusRingFilter)) == 1


def test_non_focus_events_are_ignored(marked_list):
    """The filter sees every event in the application; it must be cheap and
    inert for all of them."""
    _focus_in(marked_list, Qt.FocusReason.TabFocusReason)
    QApplication.sendEvent(marked_list, QEvent(QEvent.Type.WindowActivate))

    assert marked_list.property(KEYBOARD_FOCUS_PROPERTY) is True

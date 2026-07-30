"""Tell the stylesheet *how* a widget got focus, so the ring is keyboard-only.

The accent focus ring (D48-B) exists for keyboard users: without it, tabbing
through the application moves an invisible cursor. It was spelled as a QSS
``:focus`` rule, and that is where it went wrong. Qt's ``:focus`` pseudo-state is
true whenever a widget holds focus and carries no notion of how focus arrived, so
clicking a pane painted the ring the keyboard was supposed to get -- a 600-pixel
accent box around the curator's word list the moment you clicked a row.

The web solves this with ``:focus-visible``. Qt has no equivalent, so this module
is one: an application-wide event filter reads ``QFocusEvent.reason()`` and marks
the widget with the :data:`KEYBOARD_FOCUS_PROPERTY` dynamic property. ``common.qss``
then spells the ring ``:focus[keyboardFocus="true"]`` instead of ``:focus``.

Two details are load-bearing:

* **The mouse path never repolishes.** A ``FocusIn`` whose mark already matches
  returns after one property read. Since the mark is absent by default and a
  click never sets it, clicking around the application does no restyling at all;
  only keyboard focus pays for one.
* **Losing the window is not losing the ring.** ``FocusOut`` with
  ``ActiveWindowFocusReason`` or ``PopupFocusReason`` leaves the mark alone.
  Alt-tabbing away and back, or opening a combo box popup, would otherwise strip
  a keyboard user's ring and give it back only on the next Tab.

The QSS keeps ``:focus`` alongside the property for a reason: a mark that somehow
outlived its focus then still draws nothing.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

#: Dynamic property carried by the widget that holds *keyboard* focus. Styled in
#: ``common.qss``; named after the ``settingsSearchHit`` convention.
KEYBOARD_FOCUS_PROPERTY = "keyboardFocus"

#: The reasons that mean "a keyboard put focus here". ``Shortcut`` and ``MenuBar``
#: are included because an accelerator is a keystroke: the user's hands are on the
#: keyboard and the ring is what tells them where the keystroke landed.
KEYBOARD_FOCUS_REASONS = frozenset(
    {
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
        Qt.FocusReason.MenuBarFocusReason,
    }
)

#: ``FocusOut`` reasons that mean the widget is still the keyboard's place, it is
#: just not the active one right now.
FOCUS_OUT_REASONS_THAT_KEEP_THE_MARK = frozenset(
    {
        Qt.FocusReason.ActiveWindowFocusReason,
        Qt.FocusReason.PopupFocusReason,
    }
)

#: Object name of the installed filter, so a second install is a no-op rather
#: than a second filter doing the same work twice per focus change.
_FILTER_NAME = "keyboard-focus-ring-filter"


def _mark(widget: QWidget, on: bool) -> None:
    """Set or clear the keyboard-focus mark and restyle the widget.

    Mirrors ``settings_search._set_search_hit``: a dynamic property plus a
    repolish, because painting a state the widget is in is the stylesheet's job.
    """
    widget.setProperty(KEYBOARD_FOCUS_PROPERTY, on)
    if style := widget.style():
        style.unpolish(widget)
        style.polish(widget)


class KeyboardFocusRingFilter(QObject):
    """Marks the focused widget when, and only when, a keyboard focused it."""

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if event is None or not isinstance(obj, QWidget):
            return False

        event_type = event.type()
        if event_type not in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            return False
        if not isinstance(event, QFocusEvent):
            return False

        try:
            if event_type == QEvent.Type.FocusIn:
                wanted = event.reason() in KEYBOARD_FOCUS_REASONS
            else:
                if event.reason() in FOCUS_OUT_REASONS_THAT_KEEP_THE_MARK:
                    return False
                wanted = False

            # The early return is what keeps mouse navigation off the restyle
            # path entirely: an unmarked widget clicked into is already correct.
            if bool(obj.property(KEYBOARD_FOCUS_PROPERTY)) == wanted:
                return False

            _mark(obj, wanted)
        except RuntimeError:
            # The C++ widget went away mid-event. Nothing to mark.
            logger.debug("Focus mark skipped: widget already destroyed")

        return False


def install_keyboard_focus_ring(app: QApplication) -> KeyboardFocusRingFilter:
    """Make ``app``'s focus ring keyboard-only. Idempotent.

    Installed on the application rather than on individual widgets, so every
    dialog built later -- the word curator among them -- is covered without
    knowing this exists.
    """
    existing = app.findChild(KeyboardFocusRingFilter, _FILTER_NAME, Qt.FindChildOption.FindDirectChildrenOnly)
    if existing is not None:
        return existing

    installed = KeyboardFocusRingFilter(app)
    installed.setObjectName(_FILTER_NAME)
    app.installEventFilter(installed)
    return installed


def remove_keyboard_focus_ring(app: QApplication) -> None:
    """Take the filter back off ``app``. For tests, not for the application.

    An application-level event filter installed on the shared ``QApplication``
    outlives the test that installed it and marks widgets in every file that
    runs after it in the same pytest worker. Nothing in the application ever
    uninstalls it -- the app has exactly one, for its whole life.
    """
    existing = app.findChild(KeyboardFocusRingFilter, _FILTER_NAME, Qt.FindChildOption.FindDirectChildrenOnly)
    if existing is None:
        return
    app.removeEventFilter(existing)
    existing.setParent(None)
    existing.deleteLater()

"""Regression tests for the no-scroll protection on the header theme combo.

Issue #99: a wheel over the (unfocused) theme combo switched themes, and every
switch costs a re-measured 1647 ms whole-app stylesheet repolish. The protection
was inert because ``install_no_scroll_on_inputs(self)`` ran before
``setLayout()`` — at which point the combo is not yet a child of the header, so
the ``findChildren`` sweep matched nothing.

These tests assert the *effect* (the wheel does not move the combo), not the
mechanism: asserting focus policy or the presence of a filter object passes
even with the bug live.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.header_widget import HeaderWidget


@pytest.fixture(autouse=True)
def reset_theme_state():
    """Reset the Theme class-level singleton to a known baseline."""
    Theme.initialize(active="light", favorites=("light", "dark"), user_dir=None, state_listener=None)


def _wheel_event(widget: QComboBox, degrees: int = -120) -> QWheelEvent:
    """Build a real QWheelEvent aimed at ``widget``'s centre.

    PyQt6 requires the real class here — a stub raises a SIP ``TypeError``.
    """
    pos = QPointF(widget.rect().center())
    return QWheelEvent(
        pos,
        widget.mapToGlobal(widget.rect().center()).toPointF(),
        QPoint(0, 0),
        QPoint(0, degrees),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _build_header(qtbot) -> HeaderWidget:
    Theme.set_favorites(("light", "dark"))
    header = HeaderWidget()
    qtbot.addWidget(header)
    return header


def test_wheel_over_unfocused_theme_combo_does_not_switch_theme(qtbot):
    """A wheel delivered to the unfocused combo leaves the selection alone."""
    header = _build_header(qtbot)
    combo = header.theme_combo

    # Vacuity guard: with a single item a scroll could not move the index
    # regardless of the fix, so the assertion below would prove nothing.
    assert combo.count() >= 2

    assert not combo.hasFocus()
    before = combo.currentIndex()

    with qtbot.assertNotEmitted(header.theme_changed):
        QApplication.sendEvent(combo, _wheel_event(combo))

    assert combo.currentIndex() == before


def test_combo_is_a_child_of_the_header(qtbot):
    """The premise the inert sweep got wrong.

    ``install_no_scroll_on_inputs`` works off ``findChildren``; the combo only
    becomes a child of the header at ``setLayout``. Pins that the sweep runs at
    a point where there is something to sweep.
    """
    header = _build_header(qtbot)

    assert header.theme_combo in header.findChildren(QComboBox)

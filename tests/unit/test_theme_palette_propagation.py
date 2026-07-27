"""An application palette must reach widgets the stylesheet has already polished.

Qt 6.11 behaviour, measured rather than assumed: installing *any* non-empty
application stylesheet freezes palette propagation into the whole widget tree.
The rule does not have to match anything — a selector for a class that does not
exist is enough. From that point on ``QApplication.setPalette()`` updates the
application palette object but reaches no polished widget, so every role
``Theme.build_palette()`` fills is inert.

That matters because ``common.qss`` cannot reach most of what Qt draws: combo
popups, item delegates, spin-box and scrollbar subcontrols, the Disabled and
Inactive colour groups, dialogs Qt builds itself. Those read the palette, and a
frozen palette leaves them on the platform's colours.

``AA_UseStyleSheetPropagationInWidgetStyles`` is the only thing that unfreezes
it, and it is the precondition for decision D39-C: a theme switch cannot become
a palette swap while the palette cannot move without a full repolish.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.app import _configure_qt_application_policy

_PROPAGATION = Qt.ApplicationAttribute.AA_UseStyleSheetPropagationInWidgetStyles

#: A rule deliberately matching no widget in the tree.
_INERT_SHEET = "QClassThatDoesNotExistZZZ { font-weight: bold; }"


@pytest.fixture
def restore_attribute():
    """Leave the process-wide attribute exactly as it was found."""
    before = QApplication.testAttribute(_PROPAGATION)
    yield
    QApplication.setAttribute(_PROPAGATION, before)


@pytest.fixture
def polished_label(qapp, qtbot):
    """A label already polished under a non-empty application stylesheet."""
    sheet_before = qapp.styleSheet()
    palette_before = QPalette(qapp.palette())

    holder = QWidget()
    qtbot.addWidget(holder)
    layout = QVBoxLayout(holder)
    label = QLabel("sample", holder)
    layout.addWidget(label)

    qapp.setStyleSheet(_INERT_SHEET)
    holder.show()
    qapp.processEvents()

    yield label

    qapp.setStyleSheet(sheet_before)
    qapp.setPalette(palette_before)
    qapp.processEvents()


def _push_window_text(qapp, colour: str) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colour))
    qapp.setPalette(palette)
    qapp.processEvents()


class TestPalettePropagation:
    def test_a_stylesheet_that_matches_nothing_still_freezes_the_palette(
        self, qapp, polished_label, restore_attribute
    ) -> None:
        """The premise. Without the attribute the palette simply does not arrive."""
        QApplication.setAttribute(_PROPAGATION, False)

        _push_window_text(qapp, "#ff0000")

        assert polished_label.palette().color(QPalette.ColorRole.WindowText) != QColor("#ff0000")

    def test_the_attribute_lets_the_palette_through(self, qapp, polished_label, restore_attribute) -> None:
        QApplication.setAttribute(_PROPAGATION, True)

        _push_window_text(qapp, "#00ff00")

        assert polished_label.palette().color(QPalette.ColorRole.WindowText) == QColor("#00ff00")

    def test_a_second_palette_also_arrives(self, qapp, polished_label, restore_attribute) -> None:
        """A theme switch is not a one-shot; every later palette must land too."""
        QApplication.setAttribute(_PROPAGATION, True)

        _push_window_text(qapp, "#00ff00")
        _push_window_text(qapp, "#0000ff")

        assert polished_label.palette().color(QPalette.ColorRole.WindowText) == QColor("#0000ff")


class TestStartupPolicy:
    def test_startup_enables_palette_propagation(self, qapp, restore_attribute) -> None:
        QApplication.setAttribute(_PROPAGATION, False)

        # Qt logs one "must be called before creating the QGuiApplication"
        # warning here, for the high-DPI policy the helper also carries. That
        # is an artefact of calling startup code a second time in-process; the
        # attribute below is unaffected.
        _configure_qt_application_policy()

        assert QApplication.testAttribute(_PROPAGATION) is True

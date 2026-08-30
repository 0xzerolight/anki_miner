"""A message-box button is never narrower than the words printed on it.

``common.qss`` carried ``QMessageBox QPushButton { min-width: 80px }`` from the
initial release. Qt does not read that as a floor added to the text width: the
stylesheet engine turns it into ``QWidget::setMinimumWidth()``, and an explicit
minimum REPLACES the layout's text-derived minimum instead of expanding it
(``qSmartMinSize``: ``if (minSize.width() > 0) s.setWidth(minSize.width())``).
``QMessageBox`` then pins itself with ``setFixedSize()`` to a width computed
from the label alone, so the button row was handed the label's width and every
button whose text needed more than the floor was squeezed below its own size
hint and clipped -- ``Exclude these decks`` rendered as ``ude these de``.

Asserted as geometry, not as a grep for the selector: the trap is in what Qt
does with the declaration, so only measuring a laid-out button can tell you the
rule is gone. Any future ``min-width`` on a text-bearing control brings it back.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles.theme import Theme

THEME = "light"

# The app's own long-labelled message-box buttons, from the four call sites that
# add custom buttons. Every one of these clipped under the min-width rule, and a
# translation of any of them is longer still.
LONG_LABELS = ("Exclude these decks", "Set up resources…", "Open Log Folder", "Just this video")


@pytest.fixture
def message_box(qtbot):
    """The first-visit language prompt, stylesheet and all, shown offscreen."""
    box = QMessageBox()
    qtbot.addWidget(box)
    box.setStyleSheet(Theme.get_stylesheet(THEME))
    box.setWindowTitle("First time mining this language")
    box.setText("You have not mined 한국어 before.")
    box.setInformativeText(
        "The known-words scan reads every deck that is not excluded, so words in "
        "testing would count as already known. Exclude them from this language?"
    )
    for label in LONG_LABELS:
        box.addButton(label, QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.show()
    QApplication.processEvents()
    yield box
    box.close()


def test_no_message_box_button_is_narrower_than_its_label(message_box):
    clipped = [
        f"{button.text()!r} got {button.width()}px for a {button.sizeHint().width()}px label"
        for button in message_box.buttons()
        if button.width() < button.sizeHint().width()
    ]
    assert not clipped, "message-box buttons are clipping their own text: " + "; ".join(clipped)


def test_message_box_buttons_are_wider_than_an_in_page_button(qtbot, message_box):
    """Why the rule exists: a dialog button is chunkier than one on a screen.

    The ``min-width`` this replaced was reaching for the same thing. Horizontal
    padding gets there without capping anything, so this pins the intent rather
    than leaving the next reader to delete the rule as decoration.
    """
    host = QWidget()
    qtbot.addWidget(host)
    host.setStyleSheet(Theme.get_stylesheet(THEME))
    in_page = QPushButton(LONG_LABELS[0])
    QVBoxLayout(host).addWidget(in_page)
    host.show()
    QApplication.processEvents()

    dialog_button = next(b for b in message_box.buttons() if b.text() == LONG_LABELS[0])
    assert dialog_button.sizeHint().width() > in_page.sizeHint().width()

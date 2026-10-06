"""A modifier key one test sends must not stay held for the next test.

QTest key and mouse events that carry a modifier set the application's
keyboard-modifier state, and nothing clears it afterwards. Selection commands
issued without an event (``setCurrentCell`` on an ExtendedSelection view) read
that state, so a Ctrl shortcut test earlier on the same xdist worker left
``test_word_curation_shortcuts`` toggling two rows instead of one — red on CI
from 2026-09-30, whenever the file distribution put the two on one worker.

The autouse ``_release_held_modifiers`` fixture in ``tests/conftest.py`` clears
it. These two tests depend on running in file order, which ``--dist loadfile``
guarantees: the first leaves Ctrl held, the second is the test after it.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QLineEdit


def test_a_ctrl_shortcut_leaves_control_held(qtbot):
    edit = QLineEdit()
    qtbot.addWidget(edit)
    qtbot.keyClick(edit, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

    # The leak the fixture exists for. If Qt stops leaking, this fails and the
    # fixture can go.
    assert QGuiApplication.keyboardModifiers() == Qt.KeyboardModifier.ControlModifier


def test_b_the_next_test_starts_with_no_modifier_held(qtbot):
    assert QGuiApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier

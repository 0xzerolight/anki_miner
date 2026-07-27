"""Scoped, IME-safe shortcut primitives.

The defect these exist to prevent is live: ``word_curation_dialog.py`` creates its
Return shortcut without calling ``setContext``, so it defaults to window scope and
firing Enter to commit kana in the Search box accepts the whole review. Confirming
an action must never collide with an input method committing text, so confirmation
is ``Ctrl+Return`` (plus the keypad ``Ctrl+Enter``) and every shortcut is scoped to
the widget that owns it.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from anki_miner.gui.utils.keyboard_shortcuts import primary_action_shortcut, scoped_shortcut


class _Host(QWidget):
    """A widget with a focusable child, mirroring a dialog with a search box."""

    def __init__(self):
        super().__init__()
        self.edit = QLineEdit(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)


def _focus(qtbot, window: QWidget, target: QWidget) -> None:
    """Show ``window`` and put keyboard focus on ``target``.

    Qt resolves a shortcut from the *focus* widget upwards, so a bare
    ``keyClick`` on an unfocused widget activates nothing. Without this the
    negative assertions below would all pass vacuously.
    """
    window.show()
    qtbot.waitExposed(window)
    target.setFocus()
    qtbot.waitUntil(lambda: target.hasFocus(), timeout=1000)


class TestScopedShortcut:
    def test_defaults_to_widget_with_children_scope(self, qtbot):
        host = _Host()
        qtbot.addWidget(host)

        shortcut = scoped_shortcut(host, QKeySequence("Ctrl+D"), lambda: None)

        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut

    def test_is_retained_by_its_owner(self, qtbot):
        """An unreferenced QShortcut is collected and silently stops firing."""
        host = _Host()
        qtbot.addWidget(host)

        shortcut = scoped_shortcut(host, QKeySequence("Ctrl+D"), lambda: None)

        assert shortcut.parent() is host
        assert shortcut in host.findChildren(type(shortcut))

    def test_fires_for_its_owner(self, qtbot):
        host = _Host()
        qtbot.addWidget(host)
        fired = []
        scoped_shortcut(host, QKeySequence("Ctrl+D"), lambda: fired.append(1))
        _focus(qtbot, host, host.edit)

        qtbot.keyClick(host.edit, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

        assert fired == [1]

    def test_does_not_fire_from_an_unrelated_sibling(self, qtbot):
        """Window scope is what leaks across hidden pages; this must not."""
        parent = QWidget()
        qtbot.addWidget(parent)
        layout = QVBoxLayout(parent)
        owner, sibling = _Host(), _Host()
        layout.addWidget(owner)
        layout.addWidget(sibling)
        fired = []
        scoped_shortcut(owner, QKeySequence("Ctrl+D"), lambda: fired.append(1))
        _focus(qtbot, parent, sibling.edit)

        qtbot.keyClick(sibling.edit, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

        assert fired == []


class TestPrimaryActionShortcut:
    def test_binds_both_return_and_keypad_enter(self, qtbot):
        host = _Host()
        qtbot.addWidget(host)

        shortcuts = primary_action_shortcut(host, lambda: None)

        bound = {s.key().toString() for s in shortcuts}
        assert bound == {"Ctrl+Return", "Ctrl+Enter"}

    def test_both_are_scoped(self, qtbot):
        host = _Host()
        qtbot.addWidget(host)

        shortcuts = primary_action_shortcut(host, lambda: None)

        assert all(s.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut for s in shortcuts)

    def test_ctrl_return_confirms(self, qtbot):
        host = _Host()
        qtbot.addWidget(host)
        fired = []
        primary_action_shortcut(host, lambda: fired.append(1))
        _focus(qtbot, host, host.edit)

        qtbot.keyClick(host.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

        assert fired == [1]

    def test_bare_return_in_a_text_field_does_not_confirm(self, qtbot):
        """The whole point: Enter commits kana, it does not accept the dialog."""
        host = _Host()
        qtbot.addWidget(host)
        fired = []
        primary_action_shortcut(host, lambda: fired.append(1))
        _focus(qtbot, host, host.edit)

        qtbot.keyClick(host.edit, Qt.Key.Key_Return)

        assert fired == []

    def test_one_key_event_produces_one_activation(self, qtbot):
        """Two bindings for one action must not double-fire."""
        host = _Host()
        qtbot.addWidget(host)
        fired = []
        primary_action_shortcut(host, lambda: fired.append(1))
        _focus(qtbot, host, host.edit)

        qtbot.keyClick(host.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

        assert len(fired) == 1

    def test_a_disabled_owner_does_not_fire(self, qtbot):
        host = _Host()
        qtbot.addWidget(host)
        fired = []
        primary_action_shortcut(host, lambda: fired.append(1))
        _focus(qtbot, host, host.edit)
        host.setEnabled(False)

        qtbot.keyClick(host.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

        assert fired == []

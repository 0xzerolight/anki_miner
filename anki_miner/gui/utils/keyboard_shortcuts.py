"""Scoped, IME-safe keyboard shortcut primitives.

Two rules this module exists to enforce, both of which the app has violated:

1. **Scope every shortcut to its owner.** A ``QShortcut`` defaults to
   ``WindowShortcut``, so it fires from anywhere in the window -- including a
   hidden sibling page. ``word_curation_dialog.py`` creates its Return shortcut
   without ``setContext``, which is why pressing Enter to commit kana in the
   Search box accepts the entire review.
2. **Never confirm on bare Enter where text can have focus.** Japanese input
   methods commit composition with Enter. Confirmation is ``Ctrl+Return``, bound
   alongside the keypad's ``Ctrl+Enter`` so both physical keys work.

Shortcuts are parented to their owning widget so Qt retains them; an unreferenced
``QShortcut`` is garbage-collected and silently stops firing.
"""

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QWidget

#: Confirmation is Ctrl-modified so a bare Enter stays available to input methods.
#: "Return" is the main-block key and "Enter" is the keypad's; Qt reports them as
#: distinct keys and users hit either, so both are bound. Spelled as strings
#: because the ``Qt.Modifier.CTRL | Qt.Key`` form, while valid at runtime, does
#: not type-check.
_PRIMARY_ACTION_KEYS = (
    QKeySequence("Ctrl+Return"),
    QKeySequence("Ctrl+Enter"),
)


def scoped_shortcut(
    owner: QWidget,
    key: QKeySequence,
    slot: Callable[[], None],
    *,
    context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
) -> QShortcut:
    """Create a shortcut scoped to ``owner`` and retained by it.

    Prefer this over constructing ``QShortcut`` directly: the default Qt context
    is window-wide, which lets a shortcut fire from an unrelated page.

    Args:
        owner: Widget the shortcut belongs to and is parented to.
        key: The key sequence to bind.
        slot: Called when the shortcut activates.
        context: Activation scope. The default covers the owner and its children,
            which is what a dialog or a tab page wants.

    Returns:
        The created shortcut, already parented to ``owner``.
    """
    shortcut = QShortcut(key, owner)
    shortcut.setContext(context)
    shortcut.activated.connect(slot)
    return shortcut


def primary_action_shortcut(owner: QWidget, slot: Callable[[], None]) -> tuple[QShortcut, ...]:
    """Bind a screen's primary action to Ctrl+Return and the keypad Ctrl+Enter.

    Use this for "run this screen's main action" and for confirming any dialog
    that contains a text field. Do not add a bare-Return binding alongside it, and
    do not give such a dialog a default button -- either would re-create the
    kana-commit collision.

    Args:
        owner: Widget the shortcuts belong to.
        slot: Called when the primary action is invoked.

    Returns:
        The created shortcuts, one per bound key.
    """
    return tuple(scoped_shortcut(owner, key, slot) for key in _PRIMARY_ACTION_KEYS)

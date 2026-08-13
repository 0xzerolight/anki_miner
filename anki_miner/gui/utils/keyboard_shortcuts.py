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

3. **One source for what is bound and what is advertised.** The global
   sequences live here as constants and the About card's table is generated
   from them, so a rebinding cannot leave the printed list behind.

Shortcuts are parented to their owning widget so Qt retains them; an unreferenced
``QShortcut`` is garbage-collected and silently stops firing.
"""

from collections.abc import Callable

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QEvent, QObject, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QPushButton, QWidget

#: Opens Settings. The one global binding kept as-is: it is reachable from every
#: screen and collides with nothing.
SETTINGS_SEQUENCE = "Ctrl+,"

#: Help. F1 means help on every desktop, so it opens the *Usage Guide*; it used
#: to open About, which answers a different question.
HELP_SEQUENCE = "F1"

#: Per-tab switching, formatted with the 1-based tab number. The live tab count
#: decides how many exist -- see ``MainWindow.setup_tab_shortcuts``.
TAB_SEQUENCE_TEMPLATE = "Ctrl+{number}"

#: How the dual primary-action binding is written for a human. The two real
#: sequences are in ``_PRIMARY_ACTION_KEYS``; nobody thinks of them as two keys.
PRIMARY_ACTION_DISPLAY = "Ctrl+Enter"

#: The keyboard table the About card prints, generated from the constants above
#: so a rebinding cannot drift from what the application advertises -- which is
#: exactly how About went on offering F1 for itself after F1 became Help.
#:
#: The descriptions carry the ``AboutDialog`` context explicitly because they are
#: declared here but rendered there, and Qt resolves a translation by
#: (context, source). Deliberately a plain table and not a command registry:
#: D48-B is essentials only, with no palette and no generated command list.
SHORTCUT_HELP: tuple[tuple[str, str], ...] = (
    (
        f"{TAB_SEQUENCE_TEMPLATE.format(number=1)}..7",
        QT_TRANSLATE_NOOP("AboutDialog", "Switch tabs"),
    ),
    (SETTINGS_SEQUENCE, QT_TRANSLATE_NOOP("AboutDialog", "Open Settings")),
    (
        PRIMARY_ACTION_DISPLAY,
        QT_TRANSLATE_NOOP("AboutDialog", "Run this screen's main action"),
    ),
    (HELP_SEQUENCE, QT_TRANSLATE_NOOP("AboutDialog", "Usage Guide")),
)

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


def _strip_default_flags(dialog: QObject) -> None:
    """Clear ``default``/``autoDefault`` on every push button under ``dialog``."""
    for button in dialog.findChildren(QPushButton):
        button.setAutoDefault(False)
        button.setDefault(False)


class _DefaultButtonDisowner(QObject):
    """Re-strips the default flags each time its dialog is shown.

    One pass at construction is not enough. ``QDialogButtonBox`` promotes its
    first accept-role button to *default* from its own show handler, and
    ``QDialog`` promotes the first ``autoDefault`` button from
    ``QDialog::showEvent`` -- both run after the caller's constructor, so a
    single pre-show pass is silently undone. The dialog's own show event
    arrives after the button box has done its promotion, which is why filtering
    there wins.
    """

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if obj is not None and event is not None and event.type() == QEvent.Type.Show:
            _strip_default_flags(obj)
        return False


def disown_default_buttons(dialog: QWidget) -> None:
    """Leave ``dialog`` with no default button, now and on every show.

    A push button inside a ``QDialog`` is auto-default, and Qt clicks whichever
    one ends up default on a bare Return -- from anywhere in the dialog,
    including a Search or URL field. Return is also how a Japanese input method
    commits a composition, so leaving a default button means typing kana into a
    text field silently confirms the dialog (D49).

    Call this on every dialog that contains a text field, and give it
    :func:`primary_action_shortcut` instead when it has a confirming action.
    Every button stays reachable by mouse and by Space.

    Args:
        dialog: The dialog to disown. The show-time filter is parented to it.
    """
    _strip_default_flags(dialog)
    dialog.installEventFilter(_DefaultButtonDisowner(dialog))

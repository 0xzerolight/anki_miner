"""Remappable keyboard shortcuts: the action table and the override resolver.

Every remappable key in the app is one row of :data:`KEY_ACTIONS`. The config
keeps only the user's overrides (``config.key_bindings``: action id ->
``QKeySequence`` PortableText, ``""`` = unbound), so a default changed in a
later release reaches everyone who never touched that key. Mark known moving
from K to D relies on exactly that.

Consumers resolve where they install shortcuts:

* ``WordCurationDialog`` resolves once, at construction. An open curator keeps
  its keys until it next opens; it is rebuilt for every queue item anyway.
* ``MainWindow._apply_key_bindings`` runs at construction and on every
  ``update_config``, so an app-wide change is live at once.

Ctrl+Enter (confirm / run this screen's main action) is deliberately not here:
it is bound at a dozen sites and printed in button tooltips, so it stays fixed
(``keyboard_shortcuts.primary_action_shortcut``).

:func:`binding_problem` is the only validator, and the Settings -> Keyboard
page is its only caller. :func:`resolve_bindings` parses and never validates:
a hand-edited file gets exactly the keys it names.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt
from PyQt6.QtGui import QKeySequence

from anki_miner.gui.capabilities import MAIN_TAB_ORDER
from anki_miner.gui.utils.keyboard_shortcuts import PRIMARY_ACTION_DISPLAY

#: Context every action label is extracted and translated under.
TRANSLATION_CONTEXT = "KeyBindings"

#: The two groups. Duplicates are refused within a group only: the curator is a
#: top-level window of its own, so the main window's keys never fire in it.
CURATOR = "curator"
APP = "app"

_PORTABLE = QKeySequence.SequenceFormat.PortableText
_NATIVE = QKeySequence.SequenceFormat.NativeText


@dataclass(frozen=True)
class KeyAction:
    """One remappable action.

    ``label`` is English source text marked with ``QT_TRANSLATE_NOOP`` under
    :data:`TRANSLATION_CONTEXT`; translate it where it is shown. ``default`` is
    PortableText, ``""`` for an action that ships unbound.
    """

    id: str
    group: str
    label: str
    default: str


def tab_action_id(tab_key: str) -> str:
    """The id of the action that brings main tab ``tab_key`` to the front."""
    return f"app.tab.{tab_key}"


#: Every remappable action, in the order the Settings page lists them. The tab
#: rows follow ``MAIN_TAB_ORDER`` (pinned by a test), so Ctrl+N is the Nth tab.
KEY_ACTIONS: tuple[KeyAction, ...] = (
    KeyAction(
        "curator.toggle_include",
        CURATOR,
        QT_TRANSLATE_NOOP("KeyBindings", "Include or exclude the highlighted words"),
        "S",
    ),
    KeyAction("curator.mark_known", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Mark known"), "D"),
    KeyAction("curator.play_pause", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Play or pause"), "Space"),
    KeyAction("curator.edit_sentence", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Edit word and sentence"), "F2"),
    KeyAction("curator.include_visible", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Include visible"), "Ctrl+A"),
    KeyAction("curator.exclude_visible", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Exclude visible"), "Ctrl+D"),
    KeyAction("curator.next_word", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Next word"), ""),
    KeyAction("curator.previous_word", CURATOR, QT_TRANSLATE_NOOP("KeyBindings", "Previous word"), ""),
    KeyAction("app.open_settings", APP, QT_TRANSLATE_NOOP("KeyBindings", "Open Settings"), "Ctrl+,"),
    KeyAction("app.usage_guide", APP, QT_TRANSLATE_NOOP("KeyBindings", "Usage Guide"), "F1"),
    KeyAction(tab_action_id("video"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Video"), "Ctrl+1"),
    KeyAction(tab_action_id("deckbuilder"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Deck Builder"), "Ctrl+2"),
    KeyAction(tab_action_id("audiobook"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Audiobooks"), "Ctrl+3"),
    KeyAction(tab_action_id("reading"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Reading"), "Ctrl+4"),
    KeyAction(tab_action_id("analytics"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Analytics"), "Ctrl+5"),
    KeyAction(tab_action_id("subtitles"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Utilities"), "Ctrl+6"),
    KeyAction(tab_action_id("settings"), APP, QT_TRANSLATE_NOOP("KeyBindings", "Go to Settings"), "Ctrl+7"),
)

_BY_ID: dict[str, KeyAction] = {action.id: action for action in KEY_ACTIONS}


def key_action(action_id: str) -> KeyAction:
    """The table row for ``action_id``. Raises ``KeyError`` for an unknown id."""
    return _BY_ID[action_id]


def default_sequence(action_id: str) -> QKeySequence:
    """The shipped key for ``action_id``; empty for an action that ships unbound."""
    return QKeySequence.fromString(_BY_ID[action_id].default, _PORTABLE)


def display_text(sequence: QKeySequence) -> str:
    """How a key reads on screen, in the platform's own spelling (⌘ on macOS)."""
    return sequence.toString(_NATIVE)


def _parse(text: str) -> QKeySequence | None:
    """One chord of PortableText, or ``None`` when Qt cannot read it as one."""
    sequence = QKeySequence.fromString(text, _PORTABLE)
    if sequence.count() != 1 or sequence[0].key() == Qt.Key.Key_unknown:
        return None
    return sequence


def resolve_bindings(overrides: Mapping[str, str]) -> dict[str, QKeySequence]:
    """Every action's live key: its default, replaced by a readable override.

    An id no action answers to (a hand edit, another version's action) is
    ignored; ``""`` unbinds; a sequence Qt cannot read as one chord keeps the
    default. Never raises on config content.
    """
    keys = {action.id: default_sequence(action.id) for action in KEY_ACTIONS}
    for action_id, text in overrides.items():
        if action_id not in keys:
            continue
        if text == "":
            keys[action_id] = QKeySequence()
            continue
        parsed = _parse(text)
        if parsed is not None:
            keys[action_id] = parsed
    return keys


def overrides_for(keys: Mapping[str, QKeySequence]) -> dict[str, str]:
    """The ``config.key_bindings`` value that reproduces ``keys``: only what differs from a default."""
    overrides: dict[str, str] = {}
    for action in KEY_ACTIONS:
        text = keys[action.id].toString(_PORTABLE)
        if text != action.default:
            overrides[action.id] = text
    return overrides


@dataclass(frozen=True)
class BindingProblem:
    """Why a key was refused. ``other_action`` names the holder of a duplicate."""

    kind: Literal["reserved", "needs_modifier", "duplicate"]
    other_action: str = ""


_NAVIGATION_KEYS = ("Up", "Down", "Left", "Right", "PgUp", "PgDown", "Home", "End")

_WINDOW_MODIFIERS = (
    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
)


def _sequences(*texts: str) -> list[QKeySequence]:
    return [QKeySequence.fromString(text, _PORTABLE) for text in texts]


def _reserved(group: str) -> list[QKeySequence]:
    """Keys an action in ``group`` may not take.

    Built per call, not at import: the platform Copy keys come from the
    platform theme, which needs a running QGuiApplication.
    """
    copy_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.Copy)
    if group == CURATOR:
        # The table's own navigation (bare, and Shift-extending the highlight
        # that S and D act on), the window's Esc, confirm, and the table's Copy
        # shortcut (install_copy_rows). A shortcut on a key the table or the
        # audio clip editor handles itself (arrows, PgUp/PgDown) would pre-empt
        # that handling -- play/pause also sits on the player pane, where
        # Left/Right nudge the clip -- and a second shortcut on the Copy key is
        # activatedAmbiguously to Qt, so neither fires. Tab/Backtab are NOT
        # here: the Settings page's QKeySequenceEdit uses them as finishing
        # keys, so they can never be recorded.
        return [
            *_sequences(*_NAVIGATION_KEYS, *(f"Shift+{key}" for key in _NAVIGATION_KEYS)),
            *_sequences("Esc", "Return", "Enter", "Ctrl+Return", "Ctrl+Enter"),
            *copy_keys,
        ]
    # Keys a screen inside the main window already binds: the primary action on
    # every screen, Copy on every table, Ctrl+O on Single/Batch, Ctrl+Shift+A on
    # Batch, Alt+Up/Down on the queues. A window-wide duplicate kills both.
    return [
        *_sequences("Ctrl+Return", "Ctrl+Enter", "Ctrl+O", "Ctrl+Shift+A", "Alt+Up", "Alt+Down"),
        *copy_keys,
    ]


def _is_function_key(key: Qt.Key) -> bool:
    return Qt.Key.Key_F1.value <= key.value <= Qt.Key.Key_F35.value


def _is_letter_key(key: Qt.Key) -> bool:
    return Qt.Key.Key_A.value <= key.value <= Qt.Key.Key_Z.value


def binding_problem(action_id: str, sequence: QKeySequence, keys: Mapping[str, QKeySequence]) -> BindingProblem | None:
    """Why ``action_id`` may not take ``sequence`` given the live ``keys``; ``None`` if it may.

    An empty sequence (unbound) is always allowed.
    """
    if sequence.isEmpty():
        return None
    group = _BY_ID[action_id].group
    if any(sequence == reserved for reserved in _reserved(group)):
        return BindingProblem("reserved")
    if group == APP:
        chord = sequence[0]
        modifiers = chord.keyboardModifiers()
        # A window-wide bare key fires from every widget that does not take
        # letters itself: tables, lists, buttons.
        if not (modifiers & _WINDOW_MODIFIERS) and not _is_function_key(chord.key()):
            return BindingProblem("needs_modifier")
        # Bare Alt+letter collides with menu mnemonics (&Tools, &Help), and
        # mnemonic letters move per UI locale, so no letter is safe.
        if (
            modifiers & Qt.KeyboardModifier.AltModifier
            and not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            and _is_letter_key(chord.key())
        ):
            return BindingProblem("reserved")
    for other in KEY_ACTIONS:
        if other.id != action_id and other.group == group and keys[other.id] == sequence:
            return BindingProblem("duplicate", other.id)
    return None


def about_rows(keys: Mapping[str, QKeySequence]) -> list[tuple[str, str]]:
    """The About card's keyboard table, generated from the live bindings.

    Two independent lists are how About once kept advertising F1 for itself
    after F1 had become Help. D48-B: essentials only, no command list. The
    shipped Ctrl+1..7 run reads as one row, as it always has; a customised tab
    set lists each bound tab. Unbound actions are left out. Descriptions come
    back translated.
    """
    rows: list[tuple[str, str]] = []
    tab_ids = [tab_action_id(tab_key) for tab_key in MAIN_TAB_ORDER]
    if all(keys[tab_id] == default_sequence(tab_id) for tab_id in tab_ids):
        switch_tabs = QCoreApplication.translate("AboutDialog", "Switch tabs")
        rows.append((f"{display_text(keys[tab_ids[0]])}..{len(tab_ids)}", switch_tabs))
    else:
        for tab_id in tab_ids:
            if not keys[tab_id].isEmpty():
                label = QCoreApplication.translate(TRANSLATION_CONTEXT, _BY_ID[tab_id].label)
                rows.append((display_text(keys[tab_id]), label))
    if not keys["app.open_settings"].isEmpty():
        open_settings = QCoreApplication.translate("AboutDialog", "Open Settings")
        rows.append((display_text(keys["app.open_settings"]), open_settings))
    main_action = QCoreApplication.translate("AboutDialog", "Run this screen's main action")
    rows.append((PRIMARY_ACTION_DISPLAY, main_action))
    if not keys["app.usage_guide"].isEmpty():
        usage_guide = QCoreApplication.translate("AboutDialog", "Usage Guide")
        rows.append((display_text(keys["app.usage_guide"]), usage_guide))
    return rows

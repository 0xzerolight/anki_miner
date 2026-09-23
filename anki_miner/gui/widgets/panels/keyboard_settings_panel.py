"""Settings -> Keyboard: every remappable shortcut, one row per action."""

from __future__ import annotations

from functools import partial

from PyQt6.QtCore import QCoreApplication, QKeyCombination, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QHBoxLayout, QKeySequenceEdit, QLabel, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.key_bindings import (
    APP,
    CURATOR,
    KEY_ACTIONS,
    TRANSLATION_CONTEXT,
    BindingProblem,
    binding_problem,
    default_sequence,
    display_text,
    key_action,
    overrides_for,
    resolve_bindings,
)
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.utils.i18n import tr_format


class KeyboardSettingsPanel(FormPanel):
    """One row per remappable action: its key, Clear and Reset.

    Every change goes through :meth:`set_binding`, which validates it
    (``key_bindings.binding_problem``) and, when the key is taken, emits the
    whole overrides map at once. SettingsTab commits that immediately, like the
    Appearance & Language page, so this is not one of the Save-path panels. A
    refused key never sticks: the editor snaps back to the binding in force and
    the row says why underneath. An empty recording -- a modifier pressed on
    its own, or Esc -- leaves the binding as it was; only the row's Clear
    button unbinds.

    Commits happen on ``editingFinished`` only (one chord, so it fires as soon
    as the key lands) and on the row buttons. ``QKeySequenceEdit``'s own clear
    button is not used: its clear() also runs at the start of every recording
    and emits an empty keySequenceChanged, which would unbind the action before
    each new key arrived.
    """

    ANCHOR_NAMESPACE = "keyboard"

    #: The full overrides map (action id -> PortableText) after an accepted change.
    key_bindings_changed = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build one row per action, showing the shipped keys until a config is loaded."""
        super().__init__(self.tr("Keyboard"), parent=parent)
        self._keys: dict[str, QKeySequence] = resolve_bindings({})
        self._editors: dict[str, QKeySequenceEdit] = {}
        self._errors: dict[str, QLabel] = {}
        self._clear_buttons: dict[str, ModernButton] = {}
        self._reset_buttons: dict[str, ModernButton] = {}
        self._setup_fields()

    def _setup_fields(self) -> None:
        self.helper_label = QLabel(
            self.tr(
                "Click a box and press the new key. Changes apply at once. A Word Curator window that is "
                "already open keeps its keys until it next opens. The arrow keys always move between words, "
                "and Ctrl+Enter always confirms."
            )
        )
        self.helper_label.setObjectName("helper-text")
        self.helper_label.setWordWrap(True)
        self.add_widget(self.helper_label)

        self.add_section(self.tr("Word Curator"))
        for action in KEY_ACTIONS:
            if action.group == CURATOR:
                self._add_row(action.id)
        self.add_section(self.tr("Application"))
        for action in KEY_ACTIONS:
            if action.group == APP:
                self._add_row(action.id)

        self.restore_defaults_button = ModernButton(self.tr("Restore defaults"), variant="secondary")
        self.restore_defaults_button.clicked.connect(self.restore_defaults)
        self.add_widget(self.restore_defaults_button)
        self.add_stretch()

    def _add_row(self, action_id: str) -> None:
        label = QCoreApplication.translate(TRANSLATION_CONTEXT, key_action(action_id).label)

        editor = QKeySequenceEdit()
        editor.setMaximumSequenceLength(1)
        # Tab/Backtab (Qt's defaults) move focus on; Esc cancels a recording
        # instead of being recorded as the key "Esc".
        editor.setFinishingKeyCombinations(
            [QKeyCombination(Qt.Key.Key_Tab), QKeyCombination(Qt.Key.Key_Backtab), QKeyCombination(Qt.Key.Key_Escape)]
        )
        editor.setKeySequence(self._keys[action_id])
        editor.setAccessibleName(label)
        editor.editingFinished.connect(partial(self._on_editing_finished, action_id))

        clear_button = ModernButton(self.tr("Clear"), variant="ghost")
        clear_button.setToolTip(self.tr("Leave this action without a key"))
        clear_button.clicked.connect(partial(self._on_clear_clicked, action_id))
        reset_button = ModernButton(self.tr("Reset"), variant="ghost")
        reset_button.setToolTip(self.tr("Go back to this action's default key"))
        reset_button.clicked.connect(partial(self._on_reset_clicked, action_id))

        error = QLabel()
        error.setObjectName("validation-status")
        error.setProperty("status", "error")
        error.setWordWrap(True)
        error.setVisible(False)

        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(editor, 1)
        controls.addWidget(clear_button)
        controls.addWidget(reset_button)
        column.addLayout(controls)
        column.addWidget(error)

        self._editors[action_id] = editor
        self._errors[action_id] = error
        self._clear_buttons[action_id] = clear_button
        self._reset_buttons[action_id] = reset_button
        # Loop-built, so the anchor name is explicit ("keyboard.curator_mark_known").
        self.add_field(
            label,
            row,
            anchor=action_id.replace(".", "_"),
            anchor_focus=editor,
        )

    def _on_editing_finished(self, action_id: str) -> None:
        editor = self._editors[action_id]
        sequence = editor.keySequence()
        if sequence.isEmpty():
            # A modifier pressed on its own ends the recording with nothing in
            # it. That is not "unbind" -- only the row's Clear button unbinds.
            editor.setKeySequence(self._keys[action_id])
            return
        self.set_binding(action_id, sequence)

    def _on_clear_clicked(self, action_id: str, _checked: bool = False) -> None:
        self.set_binding(action_id, QKeySequence())

    def _on_reset_clicked(self, action_id: str, _checked: bool = False) -> None:
        self.set_binding(action_id, default_sequence(action_id))

    def set_binding(self, action_id: str, sequence: QKeySequence) -> bool:
        """Give ``action_id`` the key ``sequence`` if it passes validation.

        The one path the editors, the row buttons and the tests go through.
        Returns whether the key was taken. On a refusal the editor shows the
        binding still in force and the row says why.
        """
        editor = self._editors[action_id]
        if sequence == self._keys[action_id]:
            editor.setKeySequence(sequence)
            self._show_problem(action_id, None, sequence)
            return True
        problem = binding_problem(action_id, sequence, self._keys)
        if problem is not None:
            editor.setKeySequence(self._keys[action_id])
            self._show_problem(action_id, problem, sequence)
            return False
        self._keys[action_id] = sequence
        editor.setKeySequence(sequence)
        self._show_problem(action_id, None, sequence)
        self.key_bindings_changed.emit(overrides_for(self._keys))
        return True

    def restore_defaults(self) -> None:
        """Every action back to its shipped key, in one commit."""
        changed = bool(overrides_for(self._keys))
        self._keys = resolve_bindings({})
        self._repaint()
        if changed:
            self.key_bindings_changed.emit({})

    def load_from_config(self, config: AnkiMinerConfig) -> None:
        """Repaint every row from ``config`` without emitting (SettingsTab._load_config)."""
        self._keys = resolve_bindings(config.key_bindings)
        self._repaint()

    def _repaint(self) -> None:
        for action_id, editor in self._editors.items():
            editor.setKeySequence(self._keys[action_id])
            self._show_problem(action_id, None, QKeySequence())

    def _show_problem(self, action_id: str, problem: BindingProblem | None, sequence: QKeySequence) -> None:
        label = self._errors[action_id]
        if problem is None:
            label.clear()
            label.setVisible(False)
            return
        key = display_text(sequence)
        if problem.kind == "duplicate":
            holder = QCoreApplication.translate(TRANSLATION_CONTEXT, key_action(problem.other_action).label)
            text = tr_format(self.tr("%1 is already used for “%2”."), key, holder)
        elif problem.kind == "needs_modifier":
            text = tr_format(
                self.tr("%1 would fire from anywhere in the window. Add Ctrl, Alt or Meta, or use an F key."), key
            )
        else:
            text = tr_format(self.tr("%1 already does something else here, so it cannot be used."), key)
        label.setText(text)
        label.setVisible(True)

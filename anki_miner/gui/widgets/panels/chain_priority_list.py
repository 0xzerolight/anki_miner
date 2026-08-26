"""One priority list, rendered four times over (decision D13).

Dictionaries, word audio, frequency and pitch accent are all ordered chains of
sources, and each of them used to draw its own editor: six equal-width filled
buttons stretched in a row under the list, two of them full-width ``↑``/``↓``
arrows, a destructive **Remove** rendered exactly like **+ Add Dictionary**, and
one cramped right-aligned string carrying whatever metadata the row had.

This module is the replacement, and there is only one of it:

* :class:`ChainPriorityList` is the list itself. Reordering is what a list of
  priorities is *for*, so it is done by dragging a row, and the per-row arrow
  buttons are the keyboard/fallback path onto the same code.
* :class:`ChainSourceRow` is one row: the source's name on its own line, its own
  facts (format, entry count, staleness) on the line below, an enable toggle
  that says what it toggles instead of being an unlabelled 30x22 checkbox, and
  the two move arrows. The arrows are on the row and not in a toolbar under the
  list because reordering row three should not mean travelling to the bottom
  corner of the panel and back.
* :class:`ChainRowSpec` is what a panel hands in. Everything in it is already
  translated: the panels own their own ``tr`` contexts and this module makes no
  ``tr()`` call, so extraction contexts never churn when a row changes shape.

The row keeps the *exact* entry object it was built from in :attr:`ChainSourceRow.entry`.
That is what makes drag-reordering safe: after a move, the panel reads the order
off the row widgets rather than trying to reconstruct it from indices, so an
enabled flag can never be bound onto a neighbour's entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import configure_data_view
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.enhanced import ModernButton

#: Separator between the facts on a row's metadata line. A middle dot rather
#: than a comma: these are independent facts, not a list of one kind of thing.
METADATA_SEPARATOR = " · "

#: Glyphs on the two move controls. Plain BMP arrows, so no colour emoji font
#: can claim them -- see ``ChainSettingsPanelBase._REMOVE_GLYPH`` for the bin
#: that did.
MOVE_UP_GLYPH = "↑"
MOVE_DOWN_GLYPH = "↓"


@dataclass(frozen=True)
class ChainRowActions:
    """The move controls' translated strings, which are the same on every row.

    Separate from :class:`ChainRowSpec` on purpose: a spec is what *this entry*
    is, and these four strings are what the *panel* calls the action. Folding
    them into the spec would copy panel-wide action copy into all four
    ``_row_spec()`` producers. The panel builds one of these from its
    ``ChainListLabels`` and hands the same instance to every row.
    """

    move_up: str
    move_down: str
    move_up_tooltip: str = ""
    move_down_tooltip: str = ""


@dataclass(frozen=True)
class ChainRowSpec:
    """Everything one chain row displays, already translated by its panel.

    ``entry`` is the immutable config entry the row stands for. It must carry an
    ``enabled`` attribute -- every chain entry type does, and
    ``ChainSettingsPanelBase._entry_with_enabled`` is the other half of that
    contract.
    """

    entry: Any
    #: The source's name. Elided rather than wrapped: a row is one line tall.
    title: str
    #: This row's own facts -- format, entry count, source kind. Joined with
    #: :data:`METADATA_SEPARATOR`. Empty means "nothing is known about it",
    #: which is not the same as an entry count of zero.
    metadata: tuple[str, ...] = ()
    #: Label on the enable toggle.
    enabled_text: str = ""
    #: What a screen reader should announce for the toggle, naming the source.
    enabled_accessible_text: str = ""
    #: Staleness or breakage, in the theme's warning colour. Empty when fine.
    warning: str = ""
    #: Optional quiet repair action, e.g. a dictionary's Re-import.
    repair_text: str = ""
    #: Tooltip for the enable toggle.
    enabled_tooltip: str = ""
    #: Tooltip for any metadata that needs explaining, e.g. "word-based".
    metadata_tooltip: str = ""


class ChainSourceRow(QWidget):
    """One source in a priority chain: name, its own metadata, an enable toggle.

    Emits :attr:`toggled` when the user changes the enable state. Construction
    sets the initial state *before* connecting, so rebuilding a list never looks
    like a user edit.

    The move buttons sit on the row rather than in a toolbar under the list, and
    emit :attr:`move_up_requested` / :attr:`move_down_requested` carrying *this
    row*. The panel resolves the index from the widget, never from
    ``currentRow()``: a button on row 3 must move row 3 whatever is selected.
    """

    toggled = pyqtSignal()
    move_up_requested = pyqtSignal(object)
    move_down_requested = pyqtSignal(object)

    def __init__(
        self,
        spec: ChainRowSpec,
        actions: ChainRowActions,
        parent: QWidget | None = None,
    ) -> None:
        """Build the row.

        Args:
            spec: The already-translated content of this row.
            actions: The panel's already-translated move-control strings.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.entry = spec.entry
        self.warning_text = spec.warning
        self.repair_button: ModernButton | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(SPACING.xs, SPACING.xxs, SPACING.xs, SPACING.xxs)
        row.setSpacing(SPACING.sm)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)

        self.title_label = ElidingLabel(spec.title)
        self.title_label.setObjectName("chain-row-title")
        text.addWidget(self.title_label)

        # The second line is built even when it is empty, so every row in a list
        # is the same height. A list whose rows jump between one and two lines
        # is harder to scan than one that always spends the second line.
        detail = QHBoxLayout()
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(SPACING.xs)

        # Metadata and warning elide like the title does. They used to be plain
        # labels, which was survivable while the row's right edge held only a
        # checkbox; with the move buttons there too, an untruncated
        # "yomitan · 523,745 entries" plus a staleness sentence pushes the row's
        # minimum past a 1024px window at large text scales, and a QListWidget
        # answers that with a horizontal scrollbar rather than a shorter line.
        self.metadata_label = ElidingLabel(METADATA_SEPARATOR.join(spec.metadata))
        self.metadata_label.setObjectName("chain-row-meta")
        if spec.metadata_tooltip:
            # Not setToolTip: this tooltip explains the metadata rather than
            # repeating it, and elision rewrites the plain tooltip on every
            # re-render.
            self.metadata_label.set_tooltip_override(spec.metadata_tooltip)
        detail.addWidget(self.metadata_label)

        self.warning_label = ElidingLabel(spec.warning)
        self.warning_label.setObjectName("chain-row-warning")
        detail.addWidget(self.warning_label)
        detail.addStretch()
        text.addLayout(detail)

        row.addLayout(text, 1)

        self.checkbox = QCheckBox(spec.enabled_text)
        # The label makes the toggle self-describing on screen; the accessible
        # name still names the source, which the label alone cannot do when
        # eleven rows all read "Enabled".
        self.checkbox.setAccessibleName(spec.enabled_accessible_text or spec.enabled_text)
        if spec.enabled_tooltip:
            self.checkbox.setToolTip(spec.enabled_tooltip)
        self.checkbox.setChecked(bool(spec.entry.enabled))
        self.checkbox.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.checkbox.stateChanged.connect(lambda _state: self.toggled.emit())
        row.addWidget(self.checkbox)

        if spec.repair_text:
            self.repair_button = ModernButton(spec.repair_text, variant="ghost")
            row.addWidget(self.repair_button)

        self.up_button = self._move_button(
            MOVE_UP_GLYPH, actions.move_up, actions.move_up_tooltip, self.move_up_requested
        )
        row.addWidget(self.up_button)
        self.down_button = self._move_button(
            MOVE_DOWN_GLYPH, actions.move_down, actions.move_down_tooltip, self.move_down_requested
        )
        row.addWidget(self.down_button)

    def _move_button(self, glyph: str, name: str, tooltip: str, signal) -> ModernButton:
        """One glyph-only move control, named for anyone who cannot see it."""
        # ModernButton(square=True), not apply_button_size: the constructor also
        # sets the `square` Qt property that `QPushButton[square="true"]` needs
        # to give a glyph-only box a symmetric inset. Without it the global
        # button padding, which is measured for a word, leaves the arrow a
        # negative content box and clips it to a sliver.
        # `secondary` explicitly: ModernButton defaults to `primary`, and D41
        # spends the accent on exactly one task action per screen -- which on
        # these panels is Add, not a reorder arrow.
        button = ModernButton(glyph, variant="secondary", square=True)
        button.setAccessibleName(name)
        button.setToolTip(tooltip or name)
        button.clicked.connect(lambda: signal.emit(self))
        return button

    def set_move_enabled(self, *, up: bool, down: bool) -> None:
        """Switch the two move controls independently.

        Called with ``up=False`` on the first row and ``down=False`` on the
        last, so a button is only offered when it would do something, and with
        both false while a mutation owns the panel.
        """
        self.up_button.setEnabled(up)
        self.down_button.setEnabled(down)

    def other_arrow(self, button: QWidget | None) -> ModernButton | None:
        """This row's *other* move control, or ``None`` if it owns neither.

        Where focus goes when the arrow the user just pressed disables itself:
        the row reached an end, so the arrow pointing back the way it came is
        both still enabled and the one control that undoes the move. ``None``
        doubles as "not one of mine", which is how the panel finds the row that
        owns a widget without asking every row what type it is.
        """
        if button is self.up_button:
            return self.down_button
        if button is self.down_button:
            return self.up_button
        return None

    def get_enabled(self) -> bool:
        """Whether this row's source is currently switched on."""
        return self.checkbox.isChecked()


class ChainPriorityList(QListWidget):
    """A list of chain rows the user reorders by dragging them.

    Drops land *between* rows: a ``QListWidgetItem``'s default flags carry
    ``ItemIsDragEnabled`` but not ``ItemIsDropEnabled``, so Qt never resolves a
    drop onto a row and takes its ``moveRows`` path instead of re-creating the
    items from MIME data. That distinction is load-bearing here -- these rows are
    ``setItemWidget`` widgets, and a re-created item would arrive without one.

    Emits :attr:`order_changed` once per completed move, whatever moved the row.
    """

    order_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the list already configured for internal moves.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        configure_data_view(self)

        model = self.model()
        if model is not None:
            model.rowsMoved.connect(self._on_rows_moved)

    def _on_rows_moved(self, *_args: object) -> None:
        self.order_changed.emit()

    def move_row(self, source: int, target: int) -> bool:
        """Move the row at ``source`` so that it ends up at index ``target``.

        The arrow buttons call this so they travel the same code path a drag
        does -- one order-changed signal, one place that rebases the chain.

        Args:
            source: Index of the row to move.
            target: Index the row should occupy afterwards.

        Returns:
            True when the model performed the move.
        """
        model = self.model()
        if model is None:
            return False
        root = QModelIndex()
        # Qt reads the destination as an insertion point *before* the row
        # leaves, so a downward move has to name the slot after its target.
        destination = target if target < source else target + 1
        return model.moveRow(root, source, root, destination)

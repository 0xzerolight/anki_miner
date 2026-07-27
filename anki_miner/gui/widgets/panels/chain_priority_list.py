"""One priority list, rendered four times over (decision D13).

Dictionaries, word audio, frequency and pitch accent are all ordered chains of
sources, and each of them used to draw its own editor: six equal-width filled
buttons stretched in a row under the list, two of them full-width ``↑``/``↓``
arrows, a destructive **Remove** rendered exactly like **+ Add Dictionary**, and
one cramped right-aligned string carrying whatever metadata the row had.

This module is the replacement, and there is only one of it:

* :class:`ChainPriorityList` is the list itself. Reordering is what a list of
  priorities is *for*, so it is done by dragging a row, and the arrow buttons
  the panels keep are the keyboard/fallback path onto the same code.
* :class:`ChainSourceRow` is one row: the source's name on its own line, its own
  facts (format, entry count, staleness) on the line below, and an enable toggle
  that says what it toggles instead of being an unlabelled 30x22 checkbox.
* :class:`ChainRowSpec` is what a panel hands in. Everything in it is already
  translated: the panels own their own ``tr`` contexts and this module makes no
  ``tr()`` call, so extraction contexts never churn when a row changes shape.

The row keeps the *exact* entry object it was built from in :attr:`ChainSourceRow.entry`.
That is what makes drag-reordering safe: after a move, the panel reads the order
off the row widgets rather than trying to reconstruct it from indices, so an
enabled flag can never be bound onto a neighbour's entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
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
    #: Extra searchable/diagnostic keywords, unused by the widget itself.
    tags: tuple[str, ...] = field(default_factory=tuple)


class ChainSourceRow(QWidget):
    """One source in a priority chain: name, its own metadata, an enable toggle.

    Emits :attr:`toggled` when the user changes the enable state. Construction
    sets the initial state *before* connecting, so rebuilding a list never looks
    like a user edit.
    """

    toggled = pyqtSignal()

    def __init__(self, spec: ChainRowSpec, parent: QWidget | None = None) -> None:
        """Build the row.

        Args:
            spec: The already-translated content of this row.
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

        self.metadata_label = QLabel(METADATA_SEPARATOR.join(spec.metadata))
        self.metadata_label.setObjectName("chain-row-meta")
        if spec.metadata_tooltip:
            self.metadata_label.setToolTip(spec.metadata_tooltip)
        detail.addWidget(self.metadata_label)

        self.warning_label = QLabel(spec.warning)
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

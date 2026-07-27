"""The queue's manipulation surface: filters, search, counter, selection actions.

Decision D28. The app's whole purpose is batch mining, and until now the two
list queues set ``NoSelection``: a 200-item queue could be added to and cleared,
and nothing else. This bar supplies the missing verbs -- narrow the list, find a
row, and act on the rows you picked.

It owns no queue. It reports what the user asked for through its signals and
renders the counts the tab hands it, so the same bar serves both list queues
without either of them reaching into the other's model.

Its strings live here rather than in the tabs because this is a concrete widget
class: ``self.tr`` resolves to this class both at extraction time and at
runtime, which is exactly the mismatch the shared-base tr-context note in
``_queue_mining_tab_base`` warns about.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.base.sizing import apply_button_size
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.utils.i18n import tr_format

#: The five filters, in the order they are shown. ``all`` is first because it is
#: the resting state; the rest read left to right in the order a row travels
#: through them.
QUEUE_FILTERS: tuple[str, ...] = ("all", "ready", "running", "failed", "complete")


class QueueControlsBar(QWidget):
    """Filter chips, a search box, a live counter, and the selection actions.

    Signals:
        filter_changed: The chosen filter key, one of :data:`QUEUE_FILTERS`.
        search_changed: The current search text.
        run_selected: Mine the selected rows.
        retry_selected: Return the selected failed rows to Ready and mine them.
        remove_selected: Drop the selected rows from the queue.
    """

    filter_changed = pyqtSignal(str)
    search_changed = pyqtSignal(str)
    run_selected = pyqtSignal()
    retry_selected = pyqtSignal()
    remove_selected = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the bar with All active, an empty search and zeroed counts.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.filter_buttons: dict[str, ModernButton] = {}
        self._setup_ui()
        self.set_counts(total=0, ready=0, failed=0, complete=0)
        self.set_actions_enabled(run=False, retry=False, remove=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def active_filter(self) -> str:
        """Return the currently chosen filter key."""
        for key, button in self.filter_buttons.items():
            if button.isChecked():
                return key
        return "all"

    def search_text(self) -> str:
        """Return the current search text."""
        return self.search_edit.text()

    def set_counts(self, *, total: int, ready: int, failed: int, complete: int) -> None:
        """Render the queue's shape.

        Args:
            total: Rows in the queue.
            ready: Rows waiting to be mined.
            failed: Rows whose last attempt failed.
            complete: Rows that finished successfully.
        """
        self.counter_label.setText(
            tr_format(
                self.tr("%1 queued · %2 ready · %3 failed · %4 complete"),
                total,
                ready,
                failed,
                complete,
            )
        )

    def set_actions_enabled(self, *, run: bool, retry: bool, remove: bool) -> None:
        """Enable each selection action independently.

        Args:
            run: Whether the selection contains something minable.
            retry: Whether the selection contains a failed row.
            remove: Whether the selection contains a removable row.
        """
        self.run_button.setEnabled(run)
        self.retry_button.setEnabled(retry)
        self.remove_button.setEnabled(remove)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Lay out the filter/search line above the selection-action line."""
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACING.xs)

        outer.addLayout(self._build_filter_row())
        outer.addLayout(self._build_action_row())

        self.setLayout(outer)

    def _build_filter_row(self) -> QHBoxLayout:
        """Chips, search box and counter on one line."""
        row = QHBoxLayout()
        row.setSpacing(SPACING.xs)

        # Retained as an attribute: an unreferenced QButtonGroup is collected
        # and takes the exclusivity with it.
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)

        # Checkable ghost buttons rather than a bespoke chip: ``common.qss``
        # already paints ``QPushButton:checked`` with the accent, which under
        # D41 is exactly what a chosen toggle is meant to look like. A chip
        # style of its own would be a second answer to the same question.
        for key, label in (
            ("all", self.tr("All")),
            ("ready", self.tr("Ready")),
            ("running", self.tr("Running")),
            ("failed", self.tr("Failed")),
            ("complete", self.tr("Complete")),
        ):
            button = ModernButton(label, variant="ghost", parent=self)
            button.setCheckable(True)
            button.setProperty("queueFilter", key)
            apply_button_size(button)
            self._filter_group.addButton(button)
            button.clicked.connect(lambda _checked, k=key: self.filter_changed.emit(k))
            row.addWidget(button)
            self.filter_buttons[key] = button

        self.filter_buttons["all"].setChecked(True)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("queue-search")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(self.tr("Search the queue…"))
        self.search_edit.textChanged.connect(self.search_changed.emit)
        row.addWidget(self.search_edit, 1)

        self.counter_label = QLabel()
        self.counter_label.setObjectName("queue-counter")
        row.addWidget(self.counter_label)

        return row

    def _build_action_row(self) -> QHBoxLayout:
        """The three verbs that operate on the selection."""
        row = QHBoxLayout()
        row.setSpacing(SPACING.xs)

        # Quiet roles throughout: the screen's one accent belongs to Mine, and a
        # removal that only drops rows from a list is reversible (D41).
        self.run_button = ModernButton(self.tr("Run selected"), variant="secondary")
        self.run_button.setToolTip(self.tr("Mine the selected rows, in list order."))
        self.run_button.clicked.connect(self.run_selected.emit)

        self.retry_button = ModernButton(self.tr("Retry selected"), variant="secondary")
        self.retry_button.setToolTip(self.tr("Return the selected failed rows to Ready and mine them again."))
        self.retry_button.clicked.connect(self.retry_selected.emit)

        self.remove_button = ModernButton(self.tr("Remove selected"), variant="danger")
        self.remove_button.setToolTip(self.tr("Drop the selected rows from the queue."))
        self.remove_button.clicked.connect(self.remove_selected.emit)

        for button in (self.run_button, self.retry_button, self.remove_button):
            apply_button_size(button)
            row.addWidget(button)
        row.addStretch()

        return row

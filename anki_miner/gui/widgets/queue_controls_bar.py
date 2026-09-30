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
    QAbstractButton,
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

#: Rows a queue needs before its filter chips and search appear (D6 item 3). A
#: fixed count, never the viewport height, so the tools do not flicker on resize.
QUEUE_TOOLS_MIN_ROWS = 6


class QueueControlsBar(QWidget):
    """Filter chips, a search box, a live counter, and the selection actions.

    While a run is active it also carries the D29-A run row: the *Queue locked
    while processing.* badge and the Pause control. They live here
    rather than beside Mine because they are statements about the list directly
    below them — what can still be done to it, and where it will stop.

    Signals:
        filter_changed: The chosen filter key, one of :data:`QUEUE_FILTERS`.
        search_changed: The current search text.
        run_selected: Mine the selected rows.
        retry_selected: Return the selected failed rows to Ready and mine them.
        remove_selected: Drop the selected rows from the queue.
        edit_selected: Edit the one selected row (Batch only, see enable_edit_action).
        pause_requested: Stop cleanly after the item currently being mined.
        resume_requested: Continue a paused run.
    """

    filter_changed = pyqtSignal(str)
    search_changed = pyqtSignal(str)
    run_selected = pyqtSignal()
    retry_selected = pyqtSignal()
    remove_selected = pyqtSignal()
    edit_selected = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the bar with All active, an empty search and zeroed counts.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.filter_buttons: dict[str, ModernButton] = {}
        self._paused = False
        self._running = False
        self._pause_available = True
        # A01: which tools show depends on how many rows there are and how many
        # are selected, both handed in by the owning queue.
        self._row_count = 0
        self._selection_count = 0
        self._clear_button: QAbstractButton | None = None
        self.edit_button: ModernButton | None = None
        self._setup_ui()
        self.set_counts(total=0, ready=0, failed=0, complete=0)
        self.set_actions_enabled(run=False, retry=False, remove=False)
        self.set_selection_count(0)
        self.set_running(False)

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
                self.tr("%1 in queue · %2 ready · %3 failed · %4 complete"),
                total,
                ready,
                failed,
                complete,
            )
        )
        self._row_count = total
        self._apply_row_visibility()

    def set_actions_enabled(self, *, run: bool, retry: bool, remove: bool, edit: bool = False) -> None:
        """Enable each selection action independently.

        Args:
            run: Whether the selection contains something minable.
            retry: Whether the selection contains a failed row.
            remove: Whether the selection contains a removable row.
            edit: Whether the one selected row can be edited (Batch only).
        """
        self.run_button.setEnabled(run)
        self.retry_button.setEnabled(retry)
        self.remove_button.setEnabled(remove)
        if self.edit_button is not None:
            self.edit_button.setEnabled(edit)

    def set_selection_count(self, count: int) -> None:
        """Show the selection actions only while at least one row is selected (A01).

        Their enabled states still come from :meth:`set_actions_enabled`.

        Args:
            count: Selected, visible rows.
        """
        self._selection_count = count
        for button in (self.run_button, self.retry_button, self.remove_button):
            button.setVisible(count > 0)
        if self.edit_button is not None:
            # Edit acts on one row, so it appears for exactly one (D2).
            self.edit_button.setVisible(count == 1)

    def set_clear_button(self, button: QAbstractButton) -> None:
        """Host the queue's own Clear button beside the counter (A01).

        The button stays the owner's object (its connections and enabled state
        are the owner's); this bar only places it and shows it with the first row.

        Args:
            button: The queue's Clear button.
        """
        self._clear_button = button
        self._clear_slot.addWidget(button)
        self._apply_row_visibility()

    def enable_edit_action(self) -> None:
        """Add "Edit…" to the selection actions (the Batch queue, D2).

        Built on request because only Batch rows have anything to edit.
        """
        if self.edit_button is not None:
            return
        self.edit_button = ModernButton(self.tr("Edit…"), variant="secondary")
        self.edit_button.setToolTip(self.tr("Change the selected series' folders and offset."))
        apply_button_size(self.edit_button)
        self.edit_button.clicked.connect(self.edit_selected.emit)
        self._action_row.insertWidget(0, self.edit_button)
        self.edit_button.setVisible(self._selection_count == 1)
        self.edit_button.setEnabled(False)

    def set_counter_text(self, text: str) -> None:
        """Replace the counter's words; a queue that counts more than rows (Batch) says so.

        Call after :meth:`set_counts`, which still drives visibility.
        """
        self.counter_label.setText(text)

    def _apply_row_visibility(self) -> None:
        """Counter and Clear from the first row; chips and search from six (A01, D6)."""
        has_rows = self._row_count > 0
        many = self._row_count >= QUEUE_TOOLS_MIN_ROWS
        if not many:
            self._reset_view_tools()
        for button in self.filter_buttons.values():
            button.setVisible(many)
        self.search_edit.setVisible(many)
        self.counter_label.setVisible(has_rows)
        if self._clear_button is not None:
            self._clear_button.setVisible(has_rows)

    def _reset_view_tools(self) -> None:
        """Put the filter back to All and clear the search before hiding them.

        A narrowed view with its controls hidden would hide rows with no way
        back, so hiding the tools always undoes what they did.
        """
        if self.active_filter() != "all":
            self.filter_buttons["all"].setChecked(True)
            self.filter_changed.emit("all")
        if self.search_edit.text():
            self.search_edit.clear()

    def set_running(self, running: bool) -> None:
        """Freeze the queue for the duration of a run, and offer where to stop.

        D29-A. The run works from a snapshot taken when Mine was pressed, so a
        list that stayed editable underneath it was describing a different run
        from the one the progress numbers, the lock state and the final receipt
        were about. Locking is what lets all three be true at once.

        Called on every recompute during a run, so the per-run reset (Pause
        text, enabled state, availability) happens only when a run starts.

        Args:
            running: Whether a run currently owns the queue.
        """
        starting = running and not self._running
        self._running = running
        if not running:
            self._paused = False
        if starting:
            self._pause_available = True
            self.pause_button.setEnabled(True)
            self.pause_button.setText(self.tr("Pause after current item"))
            self.lock_label.setText(self.tr("Queue locked while processing."))
        self.lock_label.setVisible(running)
        self.pause_button.setVisible(running and (self._pause_available or self._paused))

    def set_pause_available(self, available: bool) -> None:
        """Offer Pause only while another item follows the one being mined (A10).

        Pausing after the last item means nothing, so the control goes. A run
        that is already paused keeps its Resume whatever follows.

        Args:
            available: Whether a Ready item follows the current one.
        """
        self._pause_available = available
        self.pause_button.setVisible(self._running and (available or self._paused))

    def set_paused(self, paused: bool, *, done: int = 0, total: int = 0) -> None:
        """Report that the run is sitting at an item boundary, and offer Resume.

        Args:
            paused: Whether the run is currently parked.
            done: Items finished before the pause landed.
            total: Items in the run.
        """
        self._paused = paused
        self.pause_button.setText(self.tr("Resume") if paused else self.tr("Pause after current item"))
        self.pause_button.setEnabled(True)
        if paused:
            self.lock_label.setText(tr_format(self.tr("Paused after %1 of %2"), done, total))
        else:
            self.lock_label.setText(self.tr("Queue locked while processing."))

    def is_paused(self) -> bool:
        """Whether the bar is currently offering Resume rather than Pause."""
        return self._paused

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Lay out filter/search, then the selection actions, then the run row."""
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACING.xs)

        outer.addLayout(self._build_filter_row())
        outer.addLayout(self._build_action_row())
        outer.addLayout(self._build_run_row())

        self.setLayout(outer)

    def _build_filter_row(self) -> QHBoxLayout:
        """Chips, search box and counter on one line."""
        row = QHBoxLayout()
        row.setSpacing(SPACING.xs)

        # Retained as an attribute: an unreferenced QButtonGroup is collected
        # and takes the exclusivity with it.
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)

        labels = {
            "all": self.tr("All"),
            "ready": self.tr("Ready"),
            "running": self.tr("Running"),
            "failed": self.tr("Failed"),
            "complete": self.tr("Complete"),
        }

        # Checkable ghost buttons rather than a bespoke chip: ``common.qss``
        # already paints ``QPushButton:checked`` with the accent, which under
        # D41 is exactly what a chosen toggle is meant to look like. A chip
        # style of its own would be a second answer to the same question.
        for key in QUEUE_FILTERS:
            button = ModernButton(labels[key], variant="ghost", parent=self)
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
        row.addWidget(self.search_edit, 3)

        self.counter_label = QLabel()
        self.counter_label.setObjectName("queue-counter")
        row.addWidget(self.counter_label)
        row.addStretch(1)
        # The queue's own Clear lands here (set_clear_button): Clear acts on the
        # whole list, so it sits with the counter that describes it (A01).
        self._clear_slot = QHBoxLayout()
        self._clear_slot.setContentsMargins(0, 0, 0, 0)
        row.addLayout(self._clear_slot)

        return row

    def _build_action_row(self) -> QHBoxLayout:
        """The three verbs that operate on the selection."""
        row = QHBoxLayout()
        self._action_row = row
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

    def _build_run_row(self) -> QHBoxLayout:
        """The lock badge and Pause (D29-A, D3).

        Cancel is deliberately absent: it is one verb, it lives with the run's
        primary action, and it takes no prompt (D22). Pause is the calmer
        answer -- stop between items; once paused, Cancel ends the run there.
        """
        row = QHBoxLayout()
        row.setSpacing(SPACING.xs)

        self.lock_label = QLabel()
        self.lock_label.setObjectName("queue-lock-badge")
        row.addWidget(self.lock_label)

        self.pause_button = ModernButton(self.tr("Pause after current item"), variant="secondary")
        self.pause_button.setToolTip(self.tr("The run is not cancelled — Resume continues with the next item."))
        self.pause_button.clicked.connect(self._on_pause_clicked)

        for button in (self.pause_button,):
            apply_button_size(button)
            row.addWidget(button)
        row.addStretch()

        return row

    def _on_pause_clicked(self) -> None:
        """One button, two verbs — whichever the run is not already doing."""
        if self._paused:
            self.resume_requested.emit()
        else:
            self.pause_requested.emit()

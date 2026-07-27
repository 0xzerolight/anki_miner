"""The line a finished run leaves behind on the screen that ran it (D20).

A twenty-item queue used to end in twenty modal dialogs -- one per successful
item, each waiting for a click before the next appeared -- and Batch added a
final message box that fired even after the user had cancelled. This widget is
what replaces all of it: one durable line under the progress bar, saying what
the run did, offering the details surface on request, and going away when the
user says so or when the next run starts.

Being a widget rather than a dialog is the whole point. It cannot steal focus,
it cannot interrupt, and navigating to another tab and back leaves it exactly
where it was.

All of the receipt's wording lives here, in one translation context, because it
is view text; :mod:`~anki_miner.gui.controllers.run_receipt` stays a pure model.
The count of items is printed only when there is more than one -- a single-item
screen has nothing to count, and "1 episodes" is how you tell that a template
was written for the plural case and never checked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.progress_telemetry import format_duration_words
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.models.processing import TerminalOutcome
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.controllers.run_receipt import RunReceipt


class InlineReceipt(QWidget):
    """One run's durable result, with the three things a user can do about it."""

    #: The user asked to see the run in full. The owning screen opens the
    #: details/undo surface; this widget knows nothing about dialogs.
    details_requested = pyqtSignal()
    #: The user dismissed the receipt. Not emitted when a new run clears it.
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the receipt, hidden until a run finishes.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._summary = ""
        self._receipt: RunReceipt | None = None
        self._setup_ui()
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def summary_text(self) -> str:
        """The exact line shown, and the exact line **Copy summary** copies."""
        return self._summary

    @property
    def receipt(self) -> RunReceipt | None:
        """The receipt currently shown, or None once dismissed or cleared."""
        return self._receipt

    def show_receipt(self, receipt: RunReceipt, *, item_noun: str = "") -> None:
        """Display ``receipt`` and keep it until dismissed or replaced.

        Args:
            receipt: The finished run's record.
            item_noun: The screen's own plural noun for one queue item
                ("episodes", "videos", "books"). Only used when the run had more
                than one item, so it is never made to carry a singular.
        """
        self._receipt = receipt
        self._summary = self._render(receipt, item_noun)
        self.summary_label.setText(self._summary)
        self.details_button.setVisible(receipt.has_details)
        self.show()

    def clear(self) -> None:
        """Drop the receipt silently. Used when the next run starts."""
        self._receipt = None
        self._summary = ""
        self.summary_label.setText("")
        self.hide()

    # ------------------------------------------------------------------
    # Wording
    # ------------------------------------------------------------------

    def _render(self, receipt: RunReceipt, item_noun: str) -> str:
        """Compose the summary line for one outcome.

        Two shapes, not one per outcome: a clean run states what it produced, and
        every other ending states how far it got first. That "3 of 12" is the
        part the old dialogs threw away.
        """
        duration = format_duration_words(receipt.duration.active_s)
        multi = receipt.items_total > 1 and bool(item_noun)

        if receipt.outcome is TerminalOutcome.SUCCESS:
            line = (
                tr_format(
                    self.tr("Mining complete — %1 %2, %3 notes added in %4"),
                    receipt.items_completed,
                    item_noun,
                    receipt.notes_added,
                    duration,
                )
                if multi
                else tr_format(self.tr("Mining complete — %1 notes added in %2"), receipt.notes_added, duration)
            )
        else:
            lead = {
                TerminalOutcome.CANCELLED: self.tr("Cancelled"),
                TerminalOutcome.PARTIAL: self.tr("Finished with errors"),
                TerminalOutcome.FAILED: self.tr("Mining failed"),
            }[receipt.outcome]
            line = (
                tr_format(
                    self.tr("%1 — %2 of %3 %4 completed; %5 notes added in %6"),
                    lead,
                    receipt.items_completed,
                    receipt.items_total,
                    item_noun,
                    receipt.notes_added,
                    duration,
                )
                if multi
                else tr_format(self.tr("%1 — %2 notes added in %3"), lead, receipt.notes_added, duration)
            )

        if receipt.duration.suspended:
            # The clock is active time (D23). Saying so is the difference
            # between an honest 40 minutes and an unexplained missing hour.
            line = f"{line} {self.tr('(asleep time excluded)')}"
        return line

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_copy_clicked(self) -> None:
        """Put the summary line on the clipboard, verbatim."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._summary)

    def _on_dismiss_clicked(self) -> None:
        """Hide the receipt and say that the user did it."""
        self.clear()
        self.dismissed.emit()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """One elided line plus three quiet actions, on a single row."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.sm)

        self.summary_label = ElidingLabel()
        self.summary_label.setObjectName("run-receipt-line")
        layout.addWidget(self.summary_label, 1)

        # All three are quiet: the run is over, so none of them is the task
        # action of the screen (D41).
        self.details_button = ModernButton(self.tr("View details"), variant="secondary")
        self.details_button.clicked.connect(self.details_requested)
        layout.addWidget(self.details_button)

        self.copy_button = ModernButton(self.tr("Copy summary"), variant="secondary")
        self.copy_button.clicked.connect(self._on_copy_clicked)
        layout.addWidget(self.copy_button)

        self.dismiss_button = ModernButton(self.tr("Dismiss"), variant="ghost")
        self.dismiss_button.clicked.connect(self._on_dismiss_clicked)
        layout.addWidget(self.dismiss_button)

        self.setLayout(layout)

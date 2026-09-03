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

The summary line's wording lives in :mod:`~anki_miner.gui.utils.result_copy`,
not here: this widget is one of several surfaces that report a finished run, and
a formatter copied per surface is a formatter that drifts.
:mod:`~anki_miner.gui.controllers.run_receipt` stays a pure model, holding the
numbers and none of the words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import result_copy
from anki_miner.gui.utils.progress_telemetry import format_duration_words
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.enhanced import ModernButton

if TYPE_CHECKING:
    from anki_miner.gui.controllers.run_receipt import RunReceipt


class InlineReceipt(QWidget):
    """One run's durable result, with the things a user can do about it."""

    _details_origin: ClassVar[InlineReceipt | None] = None

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

    @classmethod
    def current_details_origin(cls) -> InlineReceipt | None:
        """Receipt whose synchronous details request is being handled."""
        return cls._details_origin

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
        whitelist = receipt.whitelist
        self.copy_words_button.setVisible(whitelist is not None and bool(whitelist.missing))
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
        """Turn one receipt into its line, via the shared result formatters.

        The composition lives in :mod:`~anki_miner.gui.utils.result_copy` so the
        queue screens, the Batch adapter and this widget cannot end up wording
        the same run three ways (D47-B). This method stays as the seam that maps
        a ``RunReceipt``'s fields onto that pure function.
        """
        line = result_copy.run_summary(
            receipt.outcome,
            items_completed=receipt.items_completed,
            items_total=receipt.items_total,
            item_noun=item_noun,
            notes_added=receipt.notes_added,
            duration=format_duration_words(receipt.duration.active_s),
            suspended=receipt.duration.suspended,
        )
        if receipt.whitelist is not None:
            # A whitelist run is the reason the run happened, so its tally
            # rides the same line. ElidingLabel keeps the full text in the
            # tooltip when the line no longer fits.
            clause = result_copy.whitelist_summary(len(receipt.whitelist.mined), len(receipt.whitelist.entries))
            line = f"{line} · {clause}"
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

    def _on_copy_words_clicked(self) -> None:
        """Put the unmined whitelist words on the clipboard, one per line."""
        clipboard = QApplication.clipboard()
        if clipboard is None or self._receipt is None or self._receipt.whitelist is None:
            return
        clipboard.setText(result_copy.whitelist_unmined_text(self._receipt.whitelist))

    def _on_details_clicked(self) -> None:
        """Emit the request while its owning receipt can be consumed."""
        previous = InlineReceipt._details_origin
        InlineReceipt._details_origin = self
        try:
            self.details_requested.emit()
        finally:
            InlineReceipt._details_origin = previous

    def _on_dismiss_clicked(self) -> None:
        """Hide the receipt and say that the user did it."""
        self.clear()
        self.dismissed.emit()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """One elided line plus its quiet actions, on a single row."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.sm)

        self.summary_label = ElidingLabel()
        self.summary_label.setObjectName("run-receipt-line")
        layout.addWidget(self.summary_label, 1)

        # All three are quiet: the run is over, so none of them is the task
        # action of the screen (D41).
        self.details_button = ModernButton(self.tr("View details"), variant="secondary")
        self.details_button.clicked.connect(self._on_details_clicked)
        layout.addWidget(self.details_button)

        self.copy_button = ModernButton(self.tr("Copy summary"), variant="secondary")
        self.copy_button.clicked.connect(self._on_copy_clicked)
        layout.addWidget(self.copy_button)

        # Shown only when a whitelist left words behind: that list is what the
        # user pastes into the next run's whitelist file.
        self.copy_words_button = ModernButton(self.tr("Copy unmined words"), variant="secondary")
        self.copy_words_button.setToolTip(self.tr("Whitelist words that got no card this run, one per line"))
        self.copy_words_button.clicked.connect(self._on_copy_words_clicked)
        self.copy_words_button.setVisible(False)
        layout.addWidget(self.copy_words_button)

        self.dismiss_button = ModernButton(self.tr("Dismiss"), variant="ghost")
        self.dismiss_button.clicked.connect(self._on_dismiss_clicked)
        layout.addWidget(self.dismiss_button)

        self.setLayout(layout)

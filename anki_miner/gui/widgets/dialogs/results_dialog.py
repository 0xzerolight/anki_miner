"""Enhanced dialog for displaying processing results with stat cards."""

import logging
from typing import Callable, cast

from PyQt6.QtGui import QCloseEvent, QFont
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils import result_copy
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import EnhancedDialog
from anki_miner.gui.widgets.enhanced import StatCard
from anki_miner.models import ProcessingResult, TerminalOutcome
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class ResultsDialog(EnhancedDialog):
    """Enhanced dialog displaying processing results with beautiful stat cards.

    Uses EnhancedDialog base for consistent header/footer styling.

    Features:
    - Large success/error icon and message
    - Stat cards for key metrics (words, cards, time)
    - Error display if any
    - Undo button to delete the created notes (if note ids are available).
      ``ProcessingResult.card_ids`` holds *note* ids; the words say "cards",
      the unit the rest of the app counts in (D20 item 5, an owner decision
      that reopened D46-B).
    - Copy summary, when opened from a run's receipt (D4).
    - Modern styling with card layout
    """

    def __init__(
        self,
        result: ProcessingResult,
        parent=None,
        undo_callback: Callable[[list[int]], int] | None = None,
        on_undo_committed: Callable[[int], None] | None = None,
    ):
        """Initialize the results dialog.

        Args:
            result: Processing result to display
            parent: Optional parent widget
            undo_callback: Optional BLOCKING callback that accepts card IDs and
                returns the deleted count. Run off the GUI thread — must not
                touch Qt widgets.
            on_undo_committed: Optional GUI-thread callback invoked with the
                deleted count after a successful undo (used to decrement the
                session card counter).
        """
        super().__init__(parent, title=self.tr("Processing Results"))
        self.processing_result = result
        self._undo_callback = undo_callback
        self._on_undo_committed = on_undo_committed
        self.undo_completed = False
        self._undo_in_progress = False
        self._setup_content()

    def _setup_content(self) -> None:
        """Set up the dialog content."""
        # A 600px floor the tiles can raise. setMinimumWidth would replace the
        # layout's minimum instead of flooring it, and a longer locale's tile
        # label was cut off at the window's minimum (Z.5).
        margins = self._main_layout.contentsMargins()
        self._main_layout.addStrut(600 - margins.left() - margins.right())
        self.setMinimumHeight(400)

        # Set header based on result. A successful run states what it produced
        # rather than congratulating the user for it (D47-B) -- the number is
        # the thing they opened the dialog to read, and "Success!" made them
        # find it again in the stat cards below.
        terminal_outcome = getattr(self.processing_result, "terminal_outcome", None)
        if terminal_outcome is TerminalOutcome.CANCELLED:
            self.set_header("error", self.tr("Cancelled"))
        elif terminal_outcome is TerminalOutcome.PARTIAL:
            self.set_header("error", self.tr("Finished with errors"))
        elif terminal_outcome is TerminalOutcome.FAILED:
            self.set_header("error", self.tr("Mining failed"))
        elif self.processing_result.success:
            self.set_header("complete", result_copy.created_cards(self.processing_result.cards_created))
        else:
            self.set_header("error", self.tr("Finished with errors"))

        # Statistics cards in a frame
        stats_container = QFrame()
        stats_container.setObjectName("card")
        stats_layout = QVBoxLayout()
        stats_layout.setSpacing(SPACING.md)

        # First row of stat cards
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(SPACING.md)

        # Words discovered card
        words_card = StatCard(
            value=str(self.processing_result.total_words_found),
            label=self.tr("Words Discovered"),
        )
        row1_layout.addWidget(words_card)

        # New words card
        new_words_card = StatCard(value=str(self.processing_result.new_words_found), label=self.tr("New Words"))
        row1_layout.addWidget(new_words_card)

        # Cards created card
        cards_card = StatCard(value=str(self.processing_result.cards_created), label=self.tr("Cards Created"))
        row1_layout.addWidget(cards_card)

        stats_layout.addLayout(row1_layout)

        # Second row - processing stats
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(SPACING.md)

        # Processing time card
        time_minutes = int(self.processing_result.elapsed_time // 60)
        time_seconds = int(self.processing_result.elapsed_time % 60)
        time_str = f"{time_minutes:02d}:{time_seconds:02d}"

        time_card = StatCard(value=time_str, label=self.tr("Processing Time"))
        row2_layout.addWidget(time_card)

        # Comprehension percentage card with color indicator
        comp_pct = self.processing_result.comprehension_percentage
        comp_card = StatCard(value=f"{comp_pct:.1f}%", label=self.tr("Comprehension"))
        row2_layout.addWidget(comp_card)

        stats_layout.addLayout(row2_layout)

        stats_container.setLayout(stats_layout)
        self.add_content(stats_container)

        # Errors section (if any)
        if self.processing_result.errors:
            error_header = QLabel(self.tr("Errors Occurred"))
            error_header.setObjectName("heading3")
            error_font = QFont()
            error_font.setPixelSize(FONT_SIZES.h3)
            error_font.setWeight(QFont.Weight.Bold)
            error_header.setFont(error_font)
            self.add_content(error_header)

            error_text = QTextEdit()
            error_text.setObjectName("log-widget")
            error_text.setReadOnly(True)
            error_text.setPlainText("\n".join(self.processing_result.errors))
            error_text.setMaximumHeight(150)
            self.add_content(error_text)

        # D4: Copy summary moved here from the receipt. The text is the exact
        # line on the receipt whose View details opened this window, read while
        # that request is being handled (after that the origin is gone).
        from anki_miner.gui.widgets.inline_receipt import InlineReceipt

        origin = InlineReceipt.current_details_origin()
        self._summary_text = origin.summary_text if origin is not None else ""
        self._copy_button: QPushButton | None = None
        if self._summary_text:
            self._copy_button = self.add_button(self.tr("Copy summary"), "secondary", self._on_copy_summary)

        # Add undo button if callback and card IDs are available
        if self._undo_callback and self.processing_result.card_ids:
            self._undo_button = self.add_button(
                self._undo_button_text(len(self.processing_result.card_ids)),
                "danger",
                self._on_undo_clicked,
            )

        # Add close button using EnhancedDialog method
        self._close_button = self.add_close_button(self.tr("Close"))

    def _undo_button_text(self, count: int) -> str:
        """Label the Undo button, in the singular when it deletes one card."""
        return tr_format(self.tr("Undo (%1 card)") if count == 1 else self.tr("Undo (%1 cards)"), count)

    def _on_copy_summary(self) -> None:
        """Put the receipt's summary line on the clipboard, verbatim (D4)."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._summary_text)

    def _on_undo_clicked(self) -> None:
        """Confirm, then run the card delete OFF the GUI thread.

        The delete (AnkiConnect ``delete_notes`` + known-words revert) can block
        on a slow AnkiConnect call, so it runs via ``run_off_thread`` rather than
        freezing this modal dialog. The undo button is disabled for the duration
        and updated from the done/error continuations, which run on the GUI
        thread.
        """
        if self._undo_callback is None:
            return
        count = len(self.processing_result.card_ids)
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Undo"),
            tr_format(
                (
                    self.tr("Delete %1 card from Anki? This cannot be undone; the word becomes mineable again.")
                    if count == 1
                    else self.tr("Delete %1 cards from Anki? This cannot be undone; those words become mineable again.")
                ),
                count,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        undo_callback = self._undo_callback
        card_ids = self.processing_result.card_ids
        self._undo_in_progress = True
        self._undo_button.setEnabled(False)
        self._undo_button.setText(self.tr("Undoing…"))
        self._close_button.setEnabled(False)
        run_off_thread(
            self,
            lambda: undo_callback(card_ids),
            self._on_undo_done,
            self._on_undo_error,
        )

    def _on_undo_done(self, result: object) -> None:
        """GUI-thread continuation after the off-thread delete succeeds."""
        deleted = cast(int, result)
        self._undo_in_progress = False
        self._close_button.setEnabled(True)
        self._undo_button.setText(
            tr_format(
                self.tr("Undone (%1 card deleted)") if deleted == 1 else self.tr("Undone (%1 cards deleted)"),
                deleted,
            )
        )
        self.undo_completed = True
        if self._on_undo_committed is not None:
            self._on_undo_committed(deleted)

    def _on_undo_error(self, message: str) -> None:
        """GUI-thread continuation after the off-thread delete fails."""
        self._undo_in_progress = False
        self._close_button.setEnabled(True)
        self._undo_button.setEnabled(True)
        self._undo_button.setText(self._undo_button_text(len(self.processing_result.card_ids)))
        logger.error("Undo failed: %s", message)
        # A8-34: the AnkiConnect text names causes this sentence cannot (a note
        # type, a permission), so it survives behind Details instead of the log
        # alone.
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(self.tr("Undo Failed"))
        box.setText(self.tr("Failed to delete cards. Check that Anki is running."))
        box.setDetailedText(message)
        box.exec()

    def reject(self) -> None:
        """Ignore Escape while Undo still owns run state."""
        if self._undo_in_progress:
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 - Qt override
        """Keep the modal barrier active until Undo reaches a terminal callback."""
        if self._undo_in_progress:
            if event is not None:
                event.ignore()
            return
        super().closeEvent(event)

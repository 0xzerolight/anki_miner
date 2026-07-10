"""Enhanced dialog for displaying processing results with stat cards."""

import logging
from typing import Callable, cast

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QTextEdit, QVBoxLayout

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import EnhancedDialog
from anki_miner.gui.widgets.enhanced import StatCard
from anki_miner.models import ProcessingResult
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class ResultsDialog(EnhancedDialog):
    """Enhanced dialog displaying processing results with beautiful stat cards.

    Uses EnhancedDialog base for consistent header/footer styling.

    Features:
    - Large success/error icon and message
    - Stat cards for key metrics (words, cards, time)
    - Error display if any
    - Undo button to delete created cards (if card IDs are available)
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
        self._setup_content()

    def _setup_content(self) -> None:
        """Set up the dialog content."""
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        # Set header based on result
        if self.processing_result.success:
            self.set_header("complete", self.tr("Success!"))
        else:
            self.set_header("error", self.tr("Completed with Errors"))

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

        # Processing speed card
        if self.processing_result.elapsed_time > 0:
            speed = self.processing_result.cards_created / self.processing_result.elapsed_time
            speed_card = StatCard(value=f"{speed:.1f}/sec", label=self.tr("Processing Rate"))
            row2_layout.addWidget(speed_card)

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

        # Add undo button if callback and card IDs are available
        if self._undo_callback and self.processing_result.card_ids:
            self._undo_button = self.add_button(
                tr_format(self.tr("Undo (%1 cards)"), len(self.processing_result.card_ids)),
                "danger",
                self._on_undo_clicked,
            )

        # Add close button using EnhancedDialog method
        self.add_close_button(self.tr("Close"))

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
            tr_format(self.tr("Delete %1 cards from Anki? This cannot be undone."), count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        undo_callback = self._undo_callback
        card_ids = self.processing_result.card_ids
        self._undo_button.setEnabled(False)
        self._undo_button.setText(self.tr("Undoing…"))
        run_off_thread(
            self,
            lambda: undo_callback(card_ids),
            self._on_undo_done,
            self._on_undo_error,
        )

    def _on_undo_done(self, result: object) -> None:
        """GUI-thread continuation after the off-thread delete succeeds."""
        deleted = cast(int, result)
        self._undo_button.setText(tr_format(self.tr("Undone (%1 cards deleted)"), deleted))
        self.undo_completed = True
        if self._on_undo_committed is not None:
            self._on_undo_committed(deleted)

    def _on_undo_error(self, message: str) -> None:
        """GUI-thread continuation after the off-thread delete fails."""
        self._undo_button.setEnabled(True)
        self._undo_button.setText(tr_format(self.tr("Undo (%1 cards)"), len(self.processing_result.card_ids)))
        logger.error("Undo failed: %s", message)
        QMessageBox.critical(self, self.tr("Undo Failed"), self.tr("Failed to delete cards. Check Anki is running."))

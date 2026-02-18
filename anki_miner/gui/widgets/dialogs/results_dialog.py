"""Enhanced dialog for displaying processing results with stat cards."""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import EnhancedDialog
from anki_miner.gui.widgets.enhanced import StatCard
from anki_miner.models import ProcessingResult


class ResultsDialog(EnhancedDialog):
    """Enhanced dialog displaying processing results with beautiful stat cards.

    Uses EnhancedDialog base for consistent header/footer styling.

    Features:
    - Large success/error icon and message
    - Stat cards for key metrics (words, cards, time)
    - Error display if any
    - Modern styling with card layout
    """

    def __init__(self, result: ProcessingResult, parent=None):
        """Initialize the results dialog.

        Args:
            result: Processing result to display
            parent: Optional parent widget
        """
        super().__init__(parent, title="Processing Results")
        self.processing_result = result
        self._setup_content()

    def _setup_content(self) -> None:
        """Set up the dialog content."""
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        # Set header based on result
        if self.processing_result.success:
            self.set_header("complete", "Success!", "Processing completed successfully")
        else:
            self.set_header(
                "error", "Completed with Errors", "Some issues occurred during processing"
            )

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
            icon="word",
            value=str(self.processing_result.total_words_found),
            label="Words Discovered",
        )
        row1_layout.addWidget(words_card)

        # New words card
        new_words_card = StatCard(
            icon="card", value=str(self.processing_result.new_words_found), label="New Words"
        )
        row1_layout.addWidget(new_words_card)

        # Cards created card
        cards_card = StatCard(
            icon="complete", value=str(self.processing_result.cards_created), label="Cards Created"
        )
        row1_layout.addWidget(cards_card)

        stats_layout.addLayout(row1_layout)

        # Second row - processing stats
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(SPACING.md)

        # Processing time card
        time_minutes = int(self.processing_result.elapsed_time // 60)
        time_seconds = int(self.processing_result.elapsed_time % 60)
        time_str = f"{time_minutes:02d}:{time_seconds:02d}"

        time_card = StatCard(icon="time", value=time_str, label="Processing Time")
        row2_layout.addWidget(time_card)

        # Processing speed card
        if self.processing_result.elapsed_time > 0:
            speed = self.processing_result.cards_created / self.processing_result.elapsed_time
            speed_card = StatCard(
                icon="progress", value=f"{speed:.1f}/sec", label="Processing Rate"
            )
            row2_layout.addWidget(speed_card)

        # Comprehension percentage card with color indicator
        comp_pct = self.processing_result.comprehension_percentage
        if comp_pct > 80:
            comp_icon = "complete"
        elif comp_pct >= 60:
            comp_icon = "warning"
        else:
            comp_icon = "error"
        comp_card = StatCard(icon=comp_icon, value=f"{comp_pct:.1f}%", label="Comprehension")
        row2_layout.addWidget(comp_card)

        stats_layout.addLayout(row2_layout)

        stats_container.setLayout(stats_layout)
        self.add_content(stats_container)

        # Errors section (if any)
        if self.processing_result.errors:
            error_header = QLabel(f"{IconProvider.get_icon('warning')} Errors Occurred")
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

        # Add close button using EnhancedDialog method
        self.add_close_button("Close")

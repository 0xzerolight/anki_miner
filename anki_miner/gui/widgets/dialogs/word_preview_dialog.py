"""Enhanced dialog for previewing discovered words with search, grouping, and export."""

import csv

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.models import TokenizedWord


class WordPreviewDialog(QDialog):
    """Enhanced dialog to preview discovered words with search, grouping, and statistics.

    Features:
    - Search bar to filter table by any field
    - Group by dropdown (None, Time Range, Alphabetical, Length)
    - Statistics panel showing word counts and metrics
    - Export to CSV functionality
    - Color-coded time badges
    - Modern card-based layout
    """

    def __init__(self, words: list[TokenizedWord], parent=None):
        """Initialize the word preview dialog.

        Args:
            words: List of discovered words to preview
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.all_words = words  # All words (never filtered)
        self.filtered_words = words.copy()  # Currently displayed words
        self._setup_ui()
        self._populate_table()
        self._update_statistics()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setWindowTitle(f"Word Preview - {len(self.all_words)} words found")
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        self.resize(1100, 700)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(SPACING.md)
        main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header with title
        header = SectionHeader(
            f"{IconProvider.get_icon('word')} Word Preview — {len(self.all_words)} words found",
        )
        main_layout.addWidget(header)

        # Controls section
        controls_frame = QFrame()
        controls_frame.setObjectName("card")
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        controls_layout.setSpacing(SPACING.sm)

        # Search bar
        search_label = QLabel(f"{IconProvider.get_icon('search')} Search:")
        search_label.setFont(self._create_font(12, QFont.Weight.Medium))
        controls_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by any field...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.setMinimumWidth(250)
        controls_layout.addWidget(self.search_input)

        controls_layout.addSpacing(16)

        # Group by dropdown
        group_label = QLabel(f"{IconProvider.get_icon('filter')} Group by:")
        group_label.setFont(self._create_font(12, QFont.Weight.Medium))
        controls_layout.addWidget(group_label)

        self.group_combo = QComboBox()
        self.group_combo.addItems(["None (Flat List)", "Time Range", "Alphabetical", "Word Length"])
        self.group_combo.currentIndexChanged.connect(self._on_grouping_changed)
        self.group_combo.setMinimumWidth(150)
        controls_layout.addWidget(self.group_combo)

        controls_layout.addStretch()

        # Export CSV button
        export_button = ModernButton("Export CSV", icon="save", variant="secondary")
        export_button.clicked.connect(self._on_export_csv)
        controls_layout.addWidget(export_button)

        controls_frame.setLayout(controls_layout)
        main_layout.addWidget(controls_frame)

        # Statistics panel
        self.stats_frame = QFrame()
        self.stats_frame.setObjectName("card")
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        stats_layout.setSpacing(SPACING.lg)

        # Statistics labels
        self.total_words_label = QLabel()
        self.total_words_label.setFont(self._create_font(12, QFont.Weight.Medium))
        stats_layout.addWidget(self.total_words_label)

        self.unique_lemmas_label = QLabel()
        self.unique_lemmas_label.setFont(self._create_font(12, QFont.Weight.Medium))
        stats_layout.addWidget(self.unique_lemmas_label)

        self.avg_length_label = QLabel()
        self.avg_length_label.setFont(self._create_font(12, QFont.Weight.Medium))
        stats_layout.addWidget(self.avg_length_label)

        self.time_span_label = QLabel()
        self.time_span_label.setFont(self._create_font(12, QFont.Weight.Medium))
        stats_layout.addWidget(self.time_span_label)

        stats_layout.addStretch()

        self.stats_frame.setLayout(stats_layout)
        main_layout.addWidget(self.stats_frame)

        # Table section
        table_label = QLabel(f"{IconProvider.get_icon('list')} Discovered Words")
        table_label.setObjectName("heading3")
        table_label.setFont(self._create_font(16, QFont.Weight.Bold))
        main_layout.addWidget(table_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Surface", "Lemma", "Reading", "Sentence", "Time", "Video"]
        )

        # Configure table appearance
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        # Configure column resizing
        table_header = self.table.horizontalHeader()
        if table_header:
            table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            table_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        main_layout.addWidget(self.table)

        # Footer with result count and close button
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(SPACING.sm)

        self.result_count_label = QLabel()
        self.result_count_label.setFont(self._create_font(12, QFont.Weight.Medium))
        footer_layout.addWidget(self.result_count_label)

        footer_layout.addStretch()

        close_button = ModernButton("Close", icon="close", variant="primary")
        close_button.clicked.connect(self.accept)
        close_button.setMinimumWidth(120)
        footer_layout.addWidget(close_button)

        main_layout.addLayout(footer_layout)

        self.setLayout(main_layout)

        # Add Escape key shortcut to close dialog
        escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        escape_shortcut.activated.connect(self.reject)

    def _create_font(self, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        """Create a font with specified size and weight.

        Args:
            size: Font size in pixels
            weight: Font weight

        Returns:
            QFont object
        """
        font = QFont()
        font.setPixelSize(size)
        font.setWeight(weight)
        return font

    def _populate_table(self) -> None:
        """Populate the table with filtered words."""
        # Disable sorting while populating
        self.table.setSortingEnabled(False)

        # Clear existing rows
        self.table.setRowCount(0)

        # Group words if needed
        grouping_mode = self.group_combo.currentIndex()
        if grouping_mode == 0:  # No grouping
            self._add_words_to_table(self.filtered_words)
        elif grouping_mode == 1:  # Time Range
            self._add_words_grouped_by_time()
        elif grouping_mode == 2:  # Alphabetical
            self._add_words_grouped_alphabetically()
        elif grouping_mode == 3:  # Word Length
            self._add_words_grouped_by_length()

        # Re-enable sorting
        self.table.setSortingEnabled(True)

        # Update result count
        self.result_count_label.setText(
            f"{IconProvider.get_icon('info')} Showing {len(self.filtered_words)} of "
            f"{len(self.all_words)} words"
        )

    def _add_words_to_table(
        self, words: list[TokenizedWord], group_name: str | None = None
    ) -> None:
        """Add words to the table.

        Args:
            words: List of words to add
            group_name: Optional group header name
        """
        # Add group header if specified
        if group_name:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Create group header spanning all columns
            group_item = QTableWidgetItem(f"{group_name}")
            group_item.setFont(self._create_font(13, QFont.Weight.Bold))
            group_item.setBackground(Qt.GlobalColor.lightGray)
            self.table.setItem(row, 0, group_item)
            self.table.setSpan(row, 0, 1, 6)

        # Add words
        for word in words:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Surface form
            self.table.setItem(row, 0, QTableWidgetItem(word.surface))

            # Lemma (dictionary form)
            self.table.setItem(row, 1, QTableWidgetItem(word.lemma))

            # Reading
            self.table.setItem(row, 2, QTableWidgetItem(word.reading))

            # Sentence (truncated with full text in tooltip)
            sentence = word.sentence
            display_sentence = sentence if len(sentence) <= 60 else sentence[:57] + "..."
            sentence_item = QTableWidgetItem(display_sentence)
            sentence_item.setToolTip(sentence)
            self.table.setItem(row, 3, sentence_item)

            # Time (with color-coded badge)
            time_str = self._format_time(word.start_time)
            time_item = QTableWidgetItem(time_str)
            time_item.setToolTip(
                f"Start: {word.start_time:.2f}s, End: {word.end_time:.2f}s, Duration: {word.duration:.2f}s"
            )

            # Color-code by time range
            if word.start_time < 300:  # 0-5 minutes
                time_item.setForeground(Qt.GlobalColor.blue)
            elif word.start_time < 600:  # 5-10 minutes
                time_item.setForeground(Qt.GlobalColor.darkCyan)
            elif word.start_time < 1200:  # 10-20 minutes
                time_item.setForeground(Qt.GlobalColor.darkGreen)
            else:  # 20+ minutes
                time_item.setForeground(Qt.GlobalColor.darkMagenta)

            self.table.setItem(row, 4, time_item)

            # Video file (for batch processing)
            video_name = word.video_file.name if word.video_file else "-"
            video_item = QTableWidgetItem(video_name)
            if word.video_file:
                video_item.setToolTip(str(word.video_file))
            self.table.setItem(row, 5, video_item)

    def _add_words_grouped_by_time(self) -> None:
        """Add words grouped by time ranges."""
        # Define time ranges (in seconds)
        ranges = [
            (0, 300, "0:00 - 5:00"),
            (300, 600, "5:00 - 10:00"),
            (600, 1200, "10:00 - 20:00"),
            (1200, float("inf"), "20:00+"),
        ]

        for start, end, label in ranges:
            group_words = [w for w in self.filtered_words if start <= w.start_time < end]
            if group_words:
                self._add_words_to_table(group_words, f"{label} ({len(group_words)} words)")

    def _add_words_grouped_alphabetically(self) -> None:
        """Add words grouped by first character of lemma."""
        # Group by first character
        from collections import defaultdict

        groups = defaultdict(list)

        for word in self.filtered_words:
            first_char = word.lemma[0] if word.lemma else "?"
            groups[first_char].append(word)

        # Sort groups by key and add to table
        for char in sorted(groups.keys()):
            group_words = groups[char]
            self._add_words_to_table(group_words, f"{char} ({len(group_words)} words)")

    def _add_words_grouped_by_length(self) -> None:
        """Add words grouped by word length."""
        # Define length ranges
        ranges = [
            (1, 2, "1-2 characters"),
            (3, 4, "3-4 characters"),
            (5, 6, "5-6 characters"),
            (7, float("inf"), "7+ characters"),
        ]

        for min_len, max_len, label in ranges:
            group_words = [w for w in self.filtered_words if min_len <= len(w.lemma) <= max_len]
            if group_words:
                self._add_words_to_table(group_words, f"{label} ({len(group_words)} words)")

    def _format_time(self, seconds: float) -> str:
        """Format time in seconds to MM:SS format.

        Args:
            seconds: Time in seconds

        Returns:
            Formatted time string
        """
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def _update_statistics(self) -> None:
        """Update the statistics panel."""
        if not self.filtered_words:
            self.total_words_label.setText(f"{IconProvider.get_icon('word')} 0 words")
            self.unique_lemmas_label.setText(f"{IconProvider.get_icon('card')} 0 unique")
            self.avg_length_label.setText(f"{IconProvider.get_icon('info')} Avg: 0 chars")
            self.time_span_label.setText(f"{IconProvider.get_icon('time')} Span: 00:00")
            return

        # Total words
        total = len(self.filtered_words)
        self.total_words_label.setText(f"{IconProvider.get_icon('word')} {total} words")

        # Unique lemmas
        unique_lemmas = len({w.lemma for w in self.filtered_words})
        self.unique_lemmas_label.setText(f"{IconProvider.get_icon('card')} {unique_lemmas} unique")

        # Average word length
        avg_length = sum(len(w.lemma) for w in self.filtered_words) / len(self.filtered_words)
        self.avg_length_label.setText(
            f"{IconProvider.get_icon('info')} Avg: {avg_length:.1f} chars"
        )

        # Time span
        min_time = min(w.start_time for w in self.filtered_words)
        max_time = max(w.end_time for w in self.filtered_words)
        span = max_time - min_time
        span_str = self._format_time(span)
        self.time_span_label.setText(f"{IconProvider.get_icon('time')} Span: {span_str}")

    def _on_search_changed(self, text: str) -> None:
        """Handle search text change.

        Args:
            text: Search query text
        """
        if not text:
            # No filter, show all words
            self.filtered_words = self.all_words.copy()
        else:
            # Filter words by search text (case-insensitive)
            text_lower = text.lower()
            self.filtered_words = [
                word
                for word in self.all_words
                if (
                    text_lower in word.surface.lower()
                    or text_lower in word.lemma.lower()
                    or text_lower in word.reading.lower()
                    or text_lower in word.sentence.lower()
                )
            ]

        self._populate_table()
        self._update_statistics()

    def _on_grouping_changed(self, index: int) -> None:
        """Handle grouping mode change.

        Args:
            index: Selected grouping mode index
        """
        self._populate_table()

    def _on_export_csv(self) -> None:
        """Handle export to CSV button click."""
        # Ask user for save location
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Words to CSV", "words.csv", "CSV Files (*.csv);;All Files (*)"
        )

        if not file_path:
            return  # User cancelled

        try:
            # Write CSV file
            with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)

                # Write header
                writer.writerow(
                    [
                        "Surface",
                        "Lemma",
                        "Reading",
                        "Sentence",
                        "Start Time",
                        "End Time",
                        "Duration",
                        "Video File",
                    ]
                )

                # Write data
                for word in self.filtered_words:
                    writer.writerow(
                        [
                            word.surface,
                            word.lemma,
                            word.reading,
                            word.sentence,
                            f"{word.start_time:.2f}",
                            f"{word.end_time:.2f}",
                            f"{word.duration:.2f}",
                            str(word.video_file) if word.video_file else "",
                        ]
                    )

            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {len(self.filtered_words)} words to:\n{file_path}",
            )

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to export CSV file:\n{str(e)}")

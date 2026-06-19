"""Enhanced dialog for previewing discovered words with search, grouping, and export."""

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING, Theme
from anki_miner.gui.utils.fonts import make_scaled_font
from anki_miner.gui.widgets.dialogs.export_dialog import ExportDialog
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.models import TokenizedWord
from anki_miner.models.word import WordData
from anki_miner.utils.i18n import tr_format


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

    # Base table row height at font scale 1.0; scaled with the global UI font
    # scale so rows grow with the (QSS-driven) cell font instead of clipping it.
    _BASE_ROW_HEIGHT = 32

    def __init__(self, words: list[TokenizedWord], config: AnkiMinerConfig, parent=None):
        """Initialize the word preview dialog.

        Args:
            words: List of discovered words to preview
            config: Application configuration
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._config = config
        self.all_words = words  # All words (never filtered)
        self.filtered_words = words.copy()  # Currently displayed words
        self._theme_colors: dict[str, str] = {}
        # Debounce search keystrokes so a fast typist doesn't rebuild the
        # table N times. 150ms is short enough to feel instant while
        # collapsing a burst of characters into one populate.
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(150)
        self._search_debounce_timer.timeout.connect(self._apply_search)
        self._setup_ui()
        self._populate_table()
        self._update_statistics()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setWindowTitle(tr_format(self.tr("Word Preview - %1 words found"), len(self.all_words)))
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        self.resize(1100, 700)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(SPACING.md)
        main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header with title
        header = SectionHeader(
            tr_format(self.tr("Word Preview: %1 words found"), len(self.all_words)),
        )
        main_layout.addWidget(header)

        # Controls section
        controls_frame = QFrame()
        controls_frame.setObjectName("card")
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        controls_layout.setSpacing(SPACING.sm)

        # Search bar
        search_label = QLabel(self.tr("Search:"))
        search_label.setFont(self._create_font(12, QFont.Weight.Medium))
        controls_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Filter by any field..."))
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.setMinimumWidth(250)
        controls_layout.addWidget(self.search_input)

        controls_layout.addSpacing(16)

        # Group by dropdown
        group_label = QLabel(self.tr("Group by:"))
        group_label.setFont(self._create_font(12, QFont.Weight.Medium))
        controls_layout.addWidget(group_label)

        self.group_combo = QComboBox()
        self.group_combo.addItems(
            [self.tr("None (Flat List)"), self.tr("Time Range"), self.tr("Alphabetical"), self.tr("Word Length")]
        )
        self.group_combo.currentIndexChanged.connect(self._on_grouping_changed)
        self.group_combo.setMinimumWidth(150)
        controls_layout.addWidget(self.group_combo)

        controls_layout.addStretch()

        # Export button
        export_button = ModernButton(self.tr("Export..."), variant="secondary")
        export_button.clicked.connect(self._on_export)
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
        table_label = QLabel(self.tr("Discovered Words"))
        table_label.setObjectName("heading3")
        table_label.setFont(self._create_font(16, QFont.Weight.Bold))
        main_layout.addWidget(table_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Surface"),
                self.tr("Lemma"),
                self.tr("Reading"),
                self.tr("Sentence"),
                self.tr("Time"),
                self.tr("Video"),
            ]
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

        self._apply_fixed_row_height()

        main_layout.addWidget(self.table)

        # Footer with result count and close button
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(SPACING.sm)

        self.result_count_label = QLabel()
        self.result_count_label.setFont(self._create_font(12, QFont.Weight.Medium))
        footer_layout.addWidget(self.result_count_label)

        footer_layout.addStretch()

        close_button = ModernButton(self.tr("Close"), variant="primary")
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
        # Delegate to the shared scale-aware helper so label fonts track the
        # global UI font scale. Computed at construction; the modal dialog is
        # recreated each open, so it picks up the current scale on next open.
        return make_scaled_font(size, weight)

    def _apply_fixed_row_height(self) -> None:
        """Set Fixed resize mode, deriving the row height from the global font scale.

        Scaling the base height by ``Theme.get_font_scale()`` tracks the same scale
        the QSS cell font uses, so enlarged fonts no longer clip. Must be re-applied
        after ``setRowCount`` because some Qt versions reset the vertical-header
        section modes there. Computed at (modal, per-open) construction — the dialog
        is recreated each open, so it picks up the current scale; no live re-scaling.
        """
        v_header = self.table.verticalHeader()
        if v_header:
            row_h = round(self._BASE_ROW_HEIGHT * Theme.get_font_scale())
            v_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            v_header.setDefaultSectionSize(row_h)
            v_header.setMinimumSectionSize(max(1, row_h - 4))

    def _populate_table(self) -> None:
        """Populate the table with filtered words."""
        # Suspend repaints + sorting across the populate to collapse O(N)
        # layout invalidations into one. Without this, large word lists
        # (>200 rows) make the search bar feel sluggish.
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(0)

            # Snapshot theme colors once so subsequent calls to _add_words_to_table
            # stay consistent even if the theme changes mid-populate.
            self._theme_colors = Theme.get_colors()

            grouping_mode = self.group_combo.currentIndex()
            if grouping_mode == 0:  # No grouping
                self._add_words_to_table(self.filtered_words)
            elif grouping_mode == 1:  # Time Range
                self._add_words_grouped_by_time()
            elif grouping_mode == 2:  # Alphabetical
                self._add_words_grouped_alphabetically()
            elif grouping_mode == 3:  # Word Length
                self._add_words_grouped_by_length()
        finally:
            # Only re-enable sorting for flat mode (index 0). In grouped modes
            # (1/2/3) the table has spanned group-header rows; re-enabling
            # sorting lets a header click scramble those rows into the data.
            self.table.setSortingEnabled(grouping_mode == 0)
            self.table.setUpdatesEnabled(True)

        # Re-apply AFTER updates are re-enabled. In flat mode, re-enabling
        # sorting resets the vertical-header resize mode to Interactive, which
        # drops the scaled Fixed row height — re-applying here keeps it in
        # effect. In grouped modes sorting stays disabled so the reset doesn't
        # occur, but re-applying is harmless and keeps both paths consistent.
        self._apply_fixed_row_height()

        # Update result count
        self.result_count_label.setText(
            tr_format(self.tr("Showing %1 of %2 words"), len(self.filtered_words), len(self.all_words))
        )

    def _add_words_to_table(self, words: list[TokenizedWord], group_name: str | None = None) -> None:
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
            group_item.setBackground(QBrush(QColor(self._theme_colors["surface-alt"])))
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
                tr_format(
                    self.tr("Start: %1s, End: %2s, Duration: %3s"),
                    f"{word.start_time:.2f}",
                    f"{word.end_time:.2f}",
                    f"{word.duration:.2f}",
                )
            )

            # Color-code by time range using theme-aware semantic tokens so the
            # cue stays legible across light, dark, and custom themes.
            if word.start_time < 300:  # 0-5 minutes
                bucket_color = self._theme_colors["info"]
            elif word.start_time < 600:  # 5-10 minutes
                bucket_color = self._theme_colors["success"]
            elif word.start_time < 1200:  # 10-20 minutes
                bucket_color = self._theme_colors["warning"]
            else:  # 20+ minutes
                bucket_color = self._theme_colors["error"]
            time_item.setForeground(QBrush(QColor(bucket_color)))

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
                self._add_words_to_table(group_words, tr_format(self.tr("%1 (%2 words)"), label, len(group_words)))

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
            self._add_words_to_table(group_words, tr_format(self.tr("%1 (%2 words)"), char, len(group_words)))

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
                self._add_words_to_table(group_words, tr_format(self.tr("%1 (%2 words)"), label, len(group_words)))

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
            self.total_words_label.setText(self.tr("0 words"))
            self.unique_lemmas_label.setText(self.tr("0 unique"))
            self.avg_length_label.setText(self.tr("Avg: 0 chars"))
            self.time_span_label.setText(self.tr("Span: 00:00"))
            return

        # Total words
        total = len(self.filtered_words)
        self.total_words_label.setText(tr_format(self.tr("%1 words"), total))

        # Unique lemmas
        unique_lemmas = len({w.lemma for w in self.filtered_words})
        self.unique_lemmas_label.setText(tr_format(self.tr("%1 unique"), unique_lemmas))

        # Average word length
        avg_length = sum(len(w.lemma) for w in self.filtered_words) / len(self.filtered_words)
        self.avg_length_label.setText(tr_format(self.tr("Avg: %1 chars"), f"{avg_length:.1f}"))

        # Time span
        min_time = min(w.start_time for w in self.filtered_words)
        max_time = max(w.end_time for w in self.filtered_words)
        span = max_time - min_time
        span_str = self._format_time(span)
        self.time_span_label.setText(tr_format(self.tr("Span: %1"), span_str))

    def _on_search_changed(self, _text: str) -> None:
        """Handle search text change.

        Restarts the debounce timer so rapid typing only triggers one
        filter+repopulate after the user pauses.
        """
        self._search_debounce_timer.start()

    def _apply_search(self) -> None:
        """Filter words by the current search text and repopulate the table."""
        text = self.search_input.text()
        if not text:
            self.filtered_words = self.all_words.copy()
        else:
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

    def _on_export(self) -> None:
        """Open the export dialog for the currently filtered words."""
        word_data = [WordData(word=w) for w in self.filtered_words]
        dialog = ExportDialog(word_data, self._config, self)
        dialog.exec()

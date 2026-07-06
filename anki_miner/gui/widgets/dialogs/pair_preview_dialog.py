"""Enhanced dialog for previewing video/subtitle file pairs before processing."""

import contextlib

from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.utils.file_pairing import FilePair
from anki_miner.utils.i18n import tr_format


class PairPreviewDialog(QDialog):
    """Enhanced dialog showing video/subtitle pairs with statistics and modern styling.

    Features:
    - Card-based layout
    - Statistics panel showing pair count and file info
    - Color-coded file type indicators
    - File size display
    - Modern button styling
    - Better table formatting
    """

    def __init__(self, pairs: list[FilePair], parent=None):
        """Initialize the pair preview dialog.

        Args:
            pairs: List of FilePair objects to display
            parent: Parent widget
        """
        super().__init__(parent)
        self.pairs = pairs
        self._setup_ui()
        add_min_max_buttons(self)

    def _setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle(tr_format(self.tr("Preview File Pairs - %1 pairs found"), len(self.pairs)))
        self.setMinimumSize(900, 600)
        self.resize(1000, 650)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(SPACING.md)
        main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header
        header = SectionHeader(
            tr_format(self.tr("File Pair Preview: %1 pairs"), len(self.pairs)),
        )
        main_layout.addWidget(header)

        # Statistics panel
        stats_frame = QFrame()
        stats_frame.setObjectName("card")
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        stats_layout.setSpacing(SPACING.lg)

        # Pair count
        pair_count_label = QLabel(tr_format(self.tr("%1 video/subtitle pairs"), len(self.pairs)))
        pair_count_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(pair_count_label)

        # Total file size
        total_size = 0
        for pair in self.pairs:
            with contextlib.suppress(OSError):
                total_size += pair.video.stat().st_size + pair.subtitle.stat().st_size
        size_str = self._format_file_size(total_size)
        size_label = QLabel(tr_format(self.tr("Total size: %1"), size_str))
        size_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(size_label)

        # File type distribution
        video_types = {pair.video.suffix.lower() for pair in self.pairs}
        subtitle_types = {pair.subtitle.suffix.lower() for pair in self.pairs}
        types_label = QLabel(
            tr_format(self.tr("Video: %1 • Subtitles: %2"), ", ".join(video_types), ", ".join(subtitle_types))
        )
        types_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(types_label)

        stats_layout.addStretch()

        stats_frame.setLayout(stats_layout)
        main_layout.addWidget(stats_frame)

        # Table section
        table_label = QLabel(self.tr("Paired Files"))
        table_label.setObjectName("heading3")
        table_label.setFont(self._create_font(16, QFont.Weight.Bold))
        main_layout.addWidget(table_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Video File"), self.tr("Video Size"), self.tr("Subtitle File"), self.tr("Subtitle Size")]
        )
        self.table.setRowCount(len(self.pairs))

        # Configure table appearance
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        # Configure column resizing
        h_header = self.table.horizontalHeader()
        if h_header:
            h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            h_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            h_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        v_header = self.table.verticalHeader()
        if v_header:
            v_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        # Populate table. Suspend repaints + sorting so the populate loop
        # doesn't trigger O(N) layout invalidations for large batch imports.
        self.table.setUpdatesEnabled(False)
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        try:
            for row, pair in enumerate(self.pairs):
                # Video file name with icon
                video_item = QTableWidgetItem(pair.video.name)
                video_item.setToolTip(str(pair.video))
                self.table.setItem(row, 0, video_item)

                # Video file size
                try:
                    video_size = pair.video.stat().st_size
                except OSError:
                    video_size = 0
                video_size_item = QTableWidgetItem(self._format_file_size(video_size))
                video_size_item.setFont(self._create_font(12))
                self.table.setItem(row, 1, video_size_item)

                # Subtitle file name with icon
                subtitle_item = QTableWidgetItem(pair.subtitle.name)
                subtitle_item.setToolTip(str(pair.subtitle))
                self.table.setItem(row, 2, subtitle_item)

                # Subtitle file size
                try:
                    subtitle_size = pair.subtitle.stat().st_size
                except OSError:
                    subtitle_size = 0
                subtitle_size_item = QTableWidgetItem(self._format_file_size(subtitle_size))
                subtitle_size_item.setFont(self._create_font(12))
                self.table.setItem(row, 3, subtitle_size_item)
        finally:
            self.table.setSortingEnabled(was_sorting)
            self.table.setUpdatesEnabled(True)

        main_layout.addWidget(self.table)

        # Footer with buttons
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(SPACING.sm)

        # Info label
        info_label = QLabel(self.tr("Pairs process in order."))
        info_label.setFont(self._create_font(12))
        footer_layout.addWidget(info_label)

        footer_layout.addStretch()

        # Cancel button
        cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setMinimumWidth(120)
        footer_layout.addWidget(cancel_button)

        # Proceed button
        proceed_button = ModernButton(self.tr("Proceed with Processing"), variant="primary")
        proceed_button.clicked.connect(self.accept)
        proceed_button.setMinimumWidth(180)
        footer_layout.addWidget(proceed_button)

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

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in bytes to human-readable format.

        Args:
            size_bytes: File size in bytes

        Returns:
            Formatted size string (e.g., "1.5 MB")
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

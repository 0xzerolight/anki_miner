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

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.utils.file_pairing import FilePair


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

    def _setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle(f"Preview File Pairs - {len(self.pairs)} pairs found")
        self.setMinimumSize(900, 600)
        self.resize(1000, 650)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(SPACING.md)
        main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header
        header = SectionHeader(
            f"{IconProvider.get_icon('library')} File Pair Preview — {len(self.pairs)} pairs",
        )
        main_layout.addWidget(header)

        # Statistics panel
        stats_frame = QFrame()
        stats_frame.setObjectName("card")
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        stats_layout.setSpacing(SPACING.lg)

        # Pair count
        pair_count_label = QLabel(
            f"{IconProvider.get_icon('video')} {len(self.pairs)} video/subtitle pairs"
        )
        pair_count_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(pair_count_label)

        # Total file size
        total_size = 0
        for pair in self.pairs:
            with contextlib.suppress(OSError):
                total_size += pair.video.stat().st_size + pair.subtitle.stat().st_size
        size_str = self._format_file_size(total_size)
        size_label = QLabel(f"{IconProvider.get_icon('info')} Total size: {size_str}")
        size_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(size_label)

        # File type distribution
        video_types = {pair.video.suffix.lower() for pair in self.pairs}
        subtitle_types = {pair.subtitle.suffix.lower() for pair in self.pairs}
        types_label = QLabel(
            f"{IconProvider.get_icon('file')} Video: {', '.join(video_types)} • "
            f"Subtitles: {', '.join(subtitle_types)}"
        )
        types_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(types_label)

        stats_layout.addStretch()

        stats_frame.setLayout(stats_layout)
        main_layout.addWidget(stats_frame)

        # Table section
        table_label = QLabel(f"{IconProvider.get_icon('list')} Paired Files")
        table_label.setObjectName("heading3")
        table_label.setFont(self._create_font(16, QFont.Weight.Bold))
        main_layout.addWidget(table_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["Video File", "Video Size", "Subtitle File", "Subtitle Size"]
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

        # Populate table
        for row, pair in enumerate(self.pairs):
            # Video file name with icon
            video_item = QTableWidgetItem(f"{IconProvider.get_icon('video')} {pair.video.name}")
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
            subtitle_item = QTableWidgetItem(
                f"{IconProvider.get_icon('subtitle')} {pair.subtitle.name}"
            )
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

        main_layout.addWidget(self.table)

        # Footer with buttons
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(SPACING.sm)

        # Info label
        info_label = QLabel(
            f"{IconProvider.get_icon('info')} All pairs will be processed sequentially"
        )
        info_label.setFont(self._create_font(12))
        footer_layout.addWidget(info_label)

        footer_layout.addStretch()

        # Cancel button
        cancel_button = ModernButton("Cancel", icon="close", variant="secondary")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setMinimumWidth(120)
        footer_layout.addWidget(cancel_button)

        # Proceed button
        proceed_button = ModernButton("Proceed with Processing", icon="play", variant="primary")
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

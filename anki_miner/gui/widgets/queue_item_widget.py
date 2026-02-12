"""Enhanced queue item widget with card-based design."""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.constants import PATH_MAX_DISPLAY_LENGTH
from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING


class QueueItemWidget(QFrame):
    """Enhanced queue item widget with card-based design.

    Features:
    - Card-based design with shadow on hover
    - Header: Anime series name + status badge
    - Body: Folder paths (truncated), episode count, statistics
    - Footer: Edit and Remove buttons
    - Status badges: Pending, Processing, Complete
    - Progress overlay when processing
    - Collapsible details
    - Visual feedback on hover

    Signals:
        removed: When user clicks remove button
        edited: When user clicks edit button
    """

    removed = pyqtSignal()
    edited = pyqtSignal()

    def __init__(self, display_name: str = "", parent=None):
        """Initialize the queue item widget.

        Args:
            display_name: Display name for this queue item
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.display_name = display_name or "Untitled Series"
        self._status = "pending"  # pending, processing, complete
        self._is_expanded = True
        self._anime_folder = ""
        self._subtitle_folder = ""
        self._episode_count = 0
        self._cards_created = 0
        self._subtitle_offset = 0.0  # Per-item subtitle offset in seconds
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setObjectName("queue-item-card")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        main_layout.setSpacing(SPACING.sm)

        # Header row: Series name + status badge
        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACING.sm)

        # Series name with icon
        self.series_label = QLabel(f"{IconProvider.get_icon('video')} {self.display_name}")
        self.series_label.setObjectName("queue-item-title")
        series_font = QFont()
        series_font.setPixelSize(FONT_SIZES.h3)
        series_font.setWeight(QFont.Weight.Bold)
        self.series_label.setFont(series_font)
        header_layout.addWidget(self.series_label)

        header_layout.addStretch()

        # Status badge
        self.status_badge = QLabel()
        self.status_badge.setObjectName("queue-status-badge")
        status_font = QFont()
        status_font.setPixelSize(FONT_SIZES.small)
        status_font.setWeight(QFont.Weight.Bold)
        self.status_badge.setFont(status_font)
        header_layout.addWidget(self.status_badge)

        main_layout.addLayout(header_layout)

        # Body: Folder paths and statistics (collapsible)
        self.body_widget = QWidget()
        body_layout = QVBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(SPACING.xs)

        # Anime folder path
        self.anime_path_label = QLabel()
        self.anime_path_label.setObjectName("queue-item-path")
        path_font = QFont()
        path_font.setPixelSize(FONT_SIZES.caption)
        self.anime_path_label.setFont(path_font)
        self.anime_path_label.setWordWrap(False)
        self.anime_path_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        body_layout.addWidget(self.anime_path_label)

        # Subtitle folder path
        self.subtitle_path_label = QLabel()
        self.subtitle_path_label.setObjectName("queue-item-path")
        self.subtitle_path_label.setFont(path_font)
        self.subtitle_path_label.setWordWrap(False)
        self.subtitle_path_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        body_layout.addWidget(self.subtitle_path_label)

        # Statistics
        self.stats_label = QLabel()
        self.stats_label.setObjectName("queue-item-stats")
        stats_font = QFont()
        stats_font.setPixelSize(FONT_SIZES.caption)
        stats_font.setWeight(QFont.Weight.Medium)
        self.stats_label.setFont(stats_font)
        self.stats_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        body_layout.addWidget(self.stats_label)

        self.body_widget.setLayout(body_layout)
        main_layout.addWidget(self.body_widget)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("queue-progress")
        self.progress_bar.setMaximumHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Footer: Action buttons
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(SPACING.xs)

        footer_layout.addStretch()

        # Edit button
        self.edit_button = QPushButton(f"{IconProvider.get_icon('edit')} Edit")
        self.edit_button.setObjectName("secondary")
        self.edit_button.clicked.connect(self.edited.emit)
        self.edit_button.setToolTip("Edit anime and subtitle folders")
        footer_layout.addWidget(self.edit_button)

        # Remove button
        self.remove_button = QPushButton(f"{IconProvider.get_icon('delete')} Remove")
        self.remove_button.setObjectName("danger")
        self.remove_button.clicked.connect(self.removed.emit)
        self.remove_button.setToolTip("Remove from queue")
        footer_layout.addWidget(self.remove_button)

        main_layout.addLayout(footer_layout)

        self.setLayout(main_layout)

        # Update initial display
        self._update_status_badge()
        self._update_paths()
        self._update_stats()

    def set_folders(self, anime_folder: Path, subtitle_folder: Path) -> None:
        """Set folder paths.

        Args:
            anime_folder: Path to anime folder
            subtitle_folder: Path to subtitle folder
        """
        self._anime_folder = str(anime_folder)
        self._subtitle_folder = str(subtitle_folder)
        self._update_paths()

    def get_folders(self) -> tuple[Path | None, Path | None]:
        """Get current folder paths.

        Returns:
            Tuple of (anime_folder, subtitle_folder) or None if not set
        """
        anime = Path(self._anime_folder) if self._anime_folder else None
        subtitle = Path(self._subtitle_folder) if self._subtitle_folder else None
        return (anime, subtitle)

    def get_status(self) -> str:
        """Get the current status of this queue item.

        Returns:
            Status string ('pending', 'processing', 'complete')
        """
        return self._status

    def set_status(self, status: str) -> None:
        """Set the status of this queue item.

        Args:
            status: Status type ('pending', 'processing', 'complete')
        """
        self._status = status
        self._update_status_badge()

        # Show/hide progress bar
        if status == "processing":
            self.progress_bar.setVisible(True)
        else:
            self.progress_bar.setVisible(False)

    def set_progress(self, current: int, total: int) -> None:
        """Set the progress of processing.

        Args:
            current: Current episode number
            total: Total episode count
        """
        if total > 0:
            percentage = int((current / total) * 100)
            self.progress_bar.setValue(percentage)

    def set_episode_count(self, count: int) -> None:
        """Set the episode count.

        Args:
            count: Number of episodes
        """
        self._episode_count = count
        self._update_stats()

    def set_cards_created(self, count: int) -> None:
        """Set the number of cards created.

        Args:
            count: Number of cards created
        """
        self._cards_created = count
        self._update_stats()

    def get_episode_count(self) -> int:
        """Get the number of episodes for this queue item.

        Returns:
            Episode count, or 0 if not set
        """
        return self._episode_count

    def get_cards_created(self) -> int:
        """Get the number of cards created for this queue item.

        Returns:
            Cards created count, or 0 if not set
        """
        return self._cards_created

    @property
    def subtitle_offset(self) -> float:
        """Get subtitle offset value.

        Returns:
            Subtitle offset in seconds
        """
        return self._subtitle_offset

    @subtitle_offset.setter
    def subtitle_offset(self, value: float) -> None:
        """Set subtitle offset value.

        Args:
            value: Subtitle offset in seconds
        """
        self._subtitle_offset = value
        self._update_stats()

    def is_configured(self) -> bool:
        """Check if this queue item has all required paths set.

        Note: This checks configuration, not processing completion.

        Returns:
            True if both anime and subtitle folders are set
        """
        anime, subtitle = self.get_folders()
        return anime is not None and subtitle is not None

    def toggle_expanded(self) -> None:
        """Toggle the expanded/collapsed state."""
        self._is_expanded = not self._is_expanded
        self.body_widget.setVisible(self._is_expanded)

    def _update_status_badge(self) -> None:
        """Update the status badge display."""
        status_map = {
            "pending": ("progress", "Pending", "pending"),
            "processing": ("loading", "Processing", "processing"),
            "complete": ("complete", "Complete", "complete"),
        }

        icon_name, text, prop_value = status_map.get(
            self._status, ("progress", "Pending", "pending")
        )
        icon = IconProvider.get_icon(icon_name)
        self.status_badge.setText(f"{icon} {text}")
        self.status_badge.setProperty("status", prop_value)

        # Force style refresh
        if style := self.status_badge.style():
            style.unpolish(self.status_badge)
            style.polish(self.status_badge)

    def _update_paths(self) -> None:
        """Update the path labels with truncation."""
        folder_icon = IconProvider.get_icon("folder")
        subtitle_icon = IconProvider.get_icon("subtitle")

        if self._anime_folder:
            # Truncate path for display
            anime_path = Path(self._anime_folder)
            display_path = self._truncate_path(str(anime_path))
            self.anime_path_label.setText(f"{folder_icon} {display_path}")
            self.anime_path_label.setToolTip(str(anime_path))
        else:
            self.anime_path_label.setText(f"{folder_icon} No anime folder selected")
            self.anime_path_label.setToolTip("")

        if self._subtitle_folder:
            # Truncate path for display
            subtitle_path = Path(self._subtitle_folder)
            display_path = self._truncate_path(str(subtitle_path))
            self.subtitle_path_label.setText(f"{subtitle_icon} {display_path}")
            self.subtitle_path_label.setToolTip(str(subtitle_path))
        else:
            self.subtitle_path_label.setText(f"{subtitle_icon} No subtitle folder selected")
            self.subtitle_path_label.setToolTip("")

    def _update_stats(self) -> None:
        """Update the statistics label."""
        stats_icon = IconProvider.get_icon("info")

        # Build offset string if non-zero
        offset_str = ""
        if self._subtitle_offset != 0.0:
            sign = "+" if self._subtitle_offset > 0 else ""
            offset_str = f" • Offset: {sign}{self._subtitle_offset:.1f}s"

        if self._status == "complete" and self._cards_created > 0:
            stats_text = f"{stats_icon} {self._episode_count} episodes • {self._cards_created} cards created{offset_str}"
        elif self._episode_count > 0:
            stats_text = (
                f"{stats_icon} {self._episode_count} episodes • Ready to process{offset_str}"
            )
        else:
            if offset_str:
                stats_text = f"{stats_icon} Not configured{offset_str}"
            else:
                stats_text = f"{stats_icon} Not configured"

        self.stats_label.setText(stats_text)

    def _truncate_path(self, path: str, max_length: int = PATH_MAX_DISPLAY_LENGTH) -> str:
        """Truncate a path string for display.

        Args:
            path: Path string to truncate
            max_length: Maximum display length

        Returns:
            Truncated path string
        """
        if len(path) <= max_length:
            return path

        # Try to keep the end of the path (more informative)
        return "..." + path[-(max_length - 3) :]

    def mousePressEvent(self, event) -> None:
        """Handle mouse press for toggling expansion.

        Args:
            event: Mouse event
        """
        # Only toggle on main card area, not on buttons
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if click was on a button
            widget = self.childAt(event.pos())
            if not isinstance(widget, QPushButton):
                self.toggle_expanded()

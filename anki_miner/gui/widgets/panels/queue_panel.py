"""Multi-anime queue panel for batch processing."""

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget

logger = logging.getLogger(__name__)


class QueuePanel(QFrame):
    """Multi-anime queue management panel.

    Handles queue display, item management, and statistics.

    Signals:
        process_requested: Emitted when user wants to process queue
        queue_changed: Emitted when queue items change (add/remove/edit)
    """

    process_requested = pyqtSignal()
    queue_changed = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the queue panel.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.setObjectName("card")
        self.queue_item_widgets = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Section header with Add button
        header = SectionHeader(
            title="Multi-Anime Queue", icon="library", action_text="Add Series", action_icon="add"
        )
        header.action_clicked.connect(self._add_series)
        layout.addWidget(header)

        # Summary statistics bar
        self.queue_stats_label = QLabel()
        self.queue_stats_label.setObjectName("queue-stats")
        stats_font = QFont()
        stats_font.setPixelSize(FONT_SIZES.body_sm)
        stats_font.setWeight(QFont.Weight.Medium)
        self.queue_stats_label.setFont(stats_font)
        self.queue_stats_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.queue_stats_label)

        # Scrollable area for queue items
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(200)
        scroll_area.setObjectName("queue-scroll")

        # Container widget for queue items
        self.queue_container = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_container)
        self.queue_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_layout.setSpacing(SPACING.sm)
        self.queue_layout.addStretch()  # Push items to top

        scroll_area.setWidget(self.queue_container)
        layout.addWidget(scroll_area)

        # Queue control buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)

        self.process_queue_button = ModernButton("Process Queue", icon="play", variant="primary")
        self.process_queue_button.clicked.connect(self.process_requested.emit)
        self.process_queue_button.setToolTip("Process all anime series in queue")
        button_layout.addWidget(self.process_queue_button)

        clear_button = ModernButton("Clear All", icon="delete", variant="ghost")
        clear_button.clicked.connect(self._clear_queue)
        clear_button.setToolTip("Remove all items from queue")
        button_layout.addWidget(clear_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Update initial stats
        self._update_stats()

    def _add_series(self) -> None:
        """Add a new anime series to the queue."""
        # Prompt for series name
        name, ok = QInputDialog.getText(
            self,
            "Add Anime Series",
            f"Enter a name for series #{len(self.queue_item_widgets) + 1}:",
            text=f"Anime Series {len(self.queue_item_widgets) + 1}",
        )
        if not ok or not name.strip():
            return

        # Create queue item widget
        widget = QueueItemWidget(display_name=name, parent=self.queue_container)

        # Connect signals
        widget.removed.connect(lambda: self._remove_item(widget))
        widget.edited.connect(lambda: self._edit_item(widget))

        # Add to layout (before the stretch)
        self.queue_layout.insertWidget(len(self.queue_item_widgets), widget)
        self.queue_item_widgets.append(widget)

        self._update_stats()
        self.queue_changed.emit()

    def _remove_item(self, widget: QueueItemWidget) -> None:
        """Remove a queue item widget.

        Args:
            widget: Widget to remove
        """
        if widget in self.queue_item_widgets:
            self.queue_item_widgets.remove(widget)
            widget.deleteLater()
            self._update_stats()
            self.queue_changed.emit()

    def _edit_item(self, widget: QueueItemWidget) -> None:
        """Edit a queue item's folders and subtitle offset.

        Args:
            widget: Widget to edit
        """
        # Create simple edit dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit: {widget.display_name}")
        dialog.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Anime folder selector
        anime_selector = FileSelector(label="Anime Folder:", file_mode=False)
        current_anime, _ = widget.get_folders()
        if current_anime:
            anime_selector.set_path(str(current_anime))
        layout.addWidget(anime_selector)

        # Subtitle folder selector
        subtitle_selector = FileSelector(label="Subtitle Folder:", file_mode=False)
        _, current_subtitle = widget.get_folders()
        if current_subtitle:
            subtitle_selector.set_path(str(current_subtitle))
        layout.addWidget(subtitle_selector)

        # Subtitle offset input
        offset_layout = QHBoxLayout()
        offset_label = QLabel("Subtitle Offset:")
        offset_label.setObjectName("field-label")
        offset_spinbox = QDoubleSpinBox()
        offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        offset_spinbox.setSingleStep(0.5)
        offset_spinbox.setValue(widget.subtitle_offset)
        offset_spinbox.setSuffix(" seconds")
        offset_spinbox.setToolTip("Adjust subtitle timing (positive = later, negative = earlier)")
        offset_layout.addWidget(offset_label)
        offset_layout.addWidget(offset_spinbox)
        offset_layout.addStretch()
        layout.addLayout(offset_layout)

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.setLayout(layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Update widget with new paths
            anime_path = anime_selector.get_path()
            subtitle_path = subtitle_selector.get_path()

            if anime_path and subtitle_path:
                widget.set_folders(Path(anime_path), Path(subtitle_path))

                # Count episodes (optional enhancement)
                from anki_miner.utils.file_pairing import FilePairMatcher

                try:
                    pairs = FilePairMatcher.find_pairs_by_episode_number(
                        Path(anime_path), Path(subtitle_path)
                    )
                    widget.set_episode_count(len(pairs))
                except Exception as e:
                    logger.warning(f"Failed to count episodes for {widget.display_name}: {e}")

            # Update subtitle offset
            widget.subtitle_offset = offset_spinbox.value()

            self.queue_changed.emit()

    def _clear_queue(self) -> None:
        """Clear all items from the queue."""
        if not self.queue_item_widgets:
            QMessageBox.information(self, "Empty Queue", "Queue is already empty")
            return

        reply = QMessageBox.question(
            self,
            "Clear Queue",
            f"Remove all {len(self.queue_item_widgets)} series from queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            for widget in self.queue_item_widgets:
                widget.deleteLater()
            self.queue_item_widgets.clear()
            self._update_stats()
            self.queue_changed.emit()

    def _update_stats(self) -> None:
        """Update the queue statistics display."""
        icon = IconProvider.get_icon("info")
        series_count = len(self.queue_item_widgets)

        # Count total episodes and cards
        total_episodes = 0
        total_cards = 0
        for widget in self.queue_item_widgets:
            total_episodes += widget.get_episode_count()
            total_cards += widget.get_cards_created()

        if series_count == 0:
            text = f"{icon} Queue is empty"
        elif total_cards > 0:
            text = f"{icon} {series_count} series - {total_episodes} episodes - {total_cards} cards created"
        else:
            text = f"{icon} {series_count} series - {total_episodes} episodes - Ready to process"

        self.queue_stats_label.setText(text)

    # === Public API ===

    def add_series_external(self) -> None:
        """Add a series (for external shortcut binding)."""
        self._add_series()

    def get_valid_pairs(self) -> list:
        """Get all valid folder pairs for processing.

        Returns:
            List of tuples: (anime_folder, subtitle_folder, display_name, subtitle_offset)
        """
        valid_pairs = []
        for widget in self.queue_item_widgets:
            anime, subtitle = widget.get_folders()
            if anime and subtitle and anime.exists() and subtitle.exists():
                valid_pairs.append((anime, subtitle, widget.display_name, widget.subtitle_offset))
        return valid_pairs

    def get_incomplete_items(self) -> list:
        """Get items with missing or invalid folders.

        Returns:
            List of (widget, issue_type) where issue_type is 'incomplete' or 'invalid'
        """
        incomplete = []
        for widget in self.queue_item_widgets:
            anime, subtitle = widget.get_folders()
            if anime and subtitle:
                # Both paths set - check if they exist on disk
                if not anime.exists() or not subtitle.exists():
                    incomplete.append((widget, "invalid"))
            else:
                # One or both paths not set
                incomplete.append((widget, "incomplete"))
        return incomplete

    def is_empty(self) -> bool:
        """Check if queue is empty.

        Returns:
            True if no items in queue
        """
        return len(self.queue_item_widgets) == 0

    def set_item_status(self, display_name: str, status: str) -> None:
        """Set status for an item by display name.

        Args:
            display_name: Item display name
            status: New status ('pending', 'processing', 'complete', 'error')
        """
        for widget in self.queue_item_widgets:
            if widget.display_name == display_name:
                widget.set_status(status)
                break

    def set_item_cards(self, display_name: str, cards_created: int) -> None:
        """Set cards created count for an item.

        Args:
            display_name: Item display name
            cards_created: Number of cards created
        """
        for widget in self.queue_item_widgets:
            if widget.display_name == display_name:
                widget.set_cards_created(cards_created)
                break
        self._update_stats()

    def set_processing_item_complete(self, cards_created: int) -> None:
        """Mark the currently processing item as complete.

        Args:
            cards_created: Number of cards created
        """
        for widget in self.queue_item_widgets:
            if widget.get_status() == "processing":
                widget.set_status("complete")
                widget.set_cards_created(cards_created)
                break
        self._update_stats()

    def update_stats(self) -> None:
        """Update queue statistics display (public method)."""
        self._update_stats()

    def set_buttons_enabled(self, enabled: bool) -> None:
        """Enable or disable control buttons.

        Args:
            enabled: Whether buttons should be enabled
        """
        self.process_queue_button.setEnabled(enabled)

    @property
    def item_count(self) -> int:
        """Get number of items in queue."""
        return len(self.queue_item_widgets)

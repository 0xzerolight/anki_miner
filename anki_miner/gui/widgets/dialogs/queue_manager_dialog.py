"""Enhanced dialog for managing batch processing queue."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget
from anki_miner.models.batch_queue import BatchQueue, QueueItemStatus


class QueueManagerDialog(QDialog):
    """Enhanced dialog for managing batch processing queue with modern UI.

    Features:
    - Card-based queue item display using QueueItemWidget
    - Statistics panel showing counts and progress
    - Modern button styling
    - Scrollable queue display
    - Better visual feedback
    """

    def __init__(self, batch_queue: BatchQueue, parent=None):
        """Initialize the queue manager dialog.

        Args:
            batch_queue: BatchQueue to manage
            parent: Parent widget
        """
        super().__init__(parent)
        self.batch_queue = batch_queue
        self._queue_widgets: dict[str, QueueItemWidget] = (
            {}
        )  # Map item IDs to QueueItemWidget instances
        self._setup_ui()
        self._refresh_queue()

    def _setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle("Batch Queue Manager")
        self.setMinimumSize(900, 600)
        self.resize(1000, 700)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(SPACING.md)
        main_layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header
        header = SectionHeader(
            f"{IconProvider.get_icon('library')} Batch Queue Manager",
        )
        main_layout.addWidget(header)

        # Statistics panel
        self.stats_frame = QFrame()
        self.stats_frame.setObjectName("card")
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        stats_layout.setSpacing(SPACING.lg)

        # Total items
        self.total_label = QLabel()
        self.total_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(self.total_label)

        # Pending count
        self.pending_label = QLabel()
        self.pending_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(self.pending_label)

        # Completed count
        self.completed_label = QLabel()
        self.completed_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(self.completed_label)

        # Total cards created
        self.cards_label = QLabel()
        self.cards_label.setFont(self._create_font(13, QFont.Weight.Medium))
        stats_layout.addWidget(self.cards_label)

        stats_layout.addStretch()

        self.stats_frame.setLayout(stats_layout)
        main_layout.addWidget(self.stats_frame)

        # Action buttons row
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(SPACING.sm)

        add_button = ModernButton("Add Series", icon="add", variant="primary")
        add_button.clicked.connect(self._add_pair)
        actions_layout.addWidget(add_button)

        clear_button = ModernButton("Clear All", icon="delete", variant="danger")
        clear_button.clicked.connect(self._clear_queue)
        actions_layout.addWidget(clear_button)

        actions_layout.addStretch()

        main_layout.addLayout(actions_layout)

        # Queue items section
        queue_label = QLabel(f"{IconProvider.get_icon('list')} Queue Items")
        queue_label.setObjectName("heading3")
        queue_label.setFont(self._create_font(16, QFont.Weight.Bold))
        main_layout.addWidget(queue_label)

        # Scrollable queue container
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.queue_container = QWidget()
        self.queue_layout = QVBoxLayout()
        self.queue_layout.setSpacing(SPACING.sm)
        self.queue_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_container.setLayout(self.queue_layout)

        scroll_area.setWidget(self.queue_container)
        main_layout.addWidget(scroll_area)

        # Empty state label (shown when queue is empty)
        self.empty_label = QLabel(
            f"{IconProvider.get_icon('info')} Queue is empty. Click 'Add Series' to begin."
        )
        self.empty_label.setObjectName("helper-text")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setFont(self._create_font(14))
        self.empty_label.setMinimumHeight(200)
        self.queue_layout.addWidget(self.empty_label)

        # Add stretch at end
        self.queue_layout.addStretch()

        # Footer with close button
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()

        close_button = ModernButton("Close", icon="close", variant="primary")
        close_button.clicked.connect(self.accept)
        close_button.setMinimumWidth(120)
        footer_layout.addWidget(close_button)

        main_layout.addLayout(footer_layout)

        self.setLayout(main_layout)

        # Add Escape key shortcut to close dialog
        escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        escape_shortcut.activated.connect(self.accept)

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

    def _add_pair(self):
        """Add a new anime/subtitle folder pair with enhanced dialog flow."""
        # Get display name
        name, ok = QInputDialog.getText(
            self, "Add Series to Queue", "Enter a name for this anime series:", text="My Anime"
        )
        if not ok or not name.strip():
            return

        name = name.strip()

        # Select anime folder
        anime_folder = QFileDialog.getExistingDirectory(
            self, "Select Anime Folder", "", QFileDialog.Option.ShowDirsOnly
        )
        if not anime_folder:
            return

        # Select subtitle folder
        subtitle_folder = QFileDialog.getExistingDirectory(
            self, "Select Subtitle Folder", "", QFileDialog.Option.ShowDirsOnly
        )
        if not subtitle_folder:
            return

        # Validate folders exist
        anime_path = Path(anime_folder)
        subtitle_path = Path(subtitle_folder)

        if not anime_path.exists() or not subtitle_path.exists():
            QMessageBox.critical(
                self, "Invalid Folders", "One or both selected folders do not exist."
            )
            return

        # Add to queue
        item = self.batch_queue.add_item(anime_path, subtitle_path, name)

        # Create widget for the new item
        self._add_queue_item_widget(item)

        # Refresh display
        self._update_statistics()
        self._update_empty_state()

    def _add_queue_item_widget(self, item):
        """Add a QueueItemWidget for a queue item.

        Args:
            item: QueueItem to create widget for
        """
        # Create queue item widget
        widget = QueueItemWidget(display_name=item.display_name)
        widget.set_folders(item.anime_folder, item.subtitle_folder)

        # Set status based on item status
        status_map = {
            QueueItemStatus.PENDING: "pending",
            QueueItemStatus.PROCESSING: "processing",
            QueueItemStatus.COMPLETED: "complete",
            QueueItemStatus.ERROR: "error",
        }
        widget.set_status(status_map.get(item.status, "pending"))

        # Set cards created if completed
        if item.status == QueueItemStatus.COMPLETED:
            widget.set_cards_created(item.cards_created)

        # Connect signals
        widget.removed.connect(lambda: self._remove_item(item.id))
        widget.edited.connect(lambda: self._edit_item(item.id))

        # Store widget reference
        self._queue_widgets[item.id] = widget

        # Insert before stretch (second to last position)
        insert_position = self.queue_layout.count() - 1
        if insert_position < 0:
            insert_position = 0
        self.queue_layout.insertWidget(insert_position, widget)

    def _remove_item(self, item_id: str):
        """Remove item from queue.

        Args:
            item_id: ID of the item to remove
        """
        reply = QMessageBox.question(
            self,
            "Remove Item",
            "Are you sure you want to remove this item from the queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Remove from queue
            self.batch_queue.remove_item(item_id)

            # Remove widget
            if item_id in self._queue_widgets:
                widget = self._queue_widgets[item_id]
                self.queue_layout.removeWidget(widget)
                widget.deleteLater()
                del self._queue_widgets[item_id]

            # Update display
            self._update_statistics()
            self._update_empty_state()

    def _edit_item(self, item_id: str):
        """Edit item folders.

        Args:
            item_id: ID of the item to edit
        """
        # Find the item
        items = self.batch_queue.get_all_items()
        item = next((i for i in items if i.id == item_id), None)
        if not item:
            return

        # Select new anime folder
        anime_folder = QFileDialog.getExistingDirectory(
            self, "Select New Anime Folder", str(item.anime_folder), QFileDialog.Option.ShowDirsOnly
        )
        if anime_folder:
            item.anime_folder = Path(anime_folder)

        # Select new subtitle folder
        subtitle_folder = QFileDialog.getExistingDirectory(
            self,
            "Select New Subtitle Folder",
            str(item.subtitle_folder),
            QFileDialog.Option.ShowDirsOnly,
        )
        if subtitle_folder:
            item.subtitle_folder = Path(subtitle_folder)

        # Update widget
        if item_id in self._queue_widgets:
            widget = self._queue_widgets[item_id]
            widget.set_folders(item.anime_folder, item.subtitle_folder)

    def _clear_queue(self):
        """Clear all items from queue."""
        if self.batch_queue.total_items == 0:
            QMessageBox.information(self, "Empty Queue", "Queue is already empty")
            return

        reply = QMessageBox.question(
            self,
            "Clear Queue",
            f"Are you sure you want to remove all {self.batch_queue.total_items} items from the queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Clear queue
            self.batch_queue.clear()

            # Remove all widgets
            for widget in self._queue_widgets.values():
                self.queue_layout.removeWidget(widget)
                widget.deleteLater()
            self._queue_widgets.clear()

            # Update display
            self._update_statistics()
            self._update_empty_state()

    def _refresh_queue(self):
        """Refresh the queue display."""
        # Clear existing widgets
        for widget in self._queue_widgets.values():
            self.queue_layout.removeWidget(widget)
            widget.deleteLater()
        self._queue_widgets.clear()

        # Add widgets for all items
        items = self.batch_queue.get_all_items()
        for item in items:
            self._add_queue_item_widget(item)

        # Update display
        self._update_statistics()
        self._update_empty_state()

    def _update_statistics(self):
        """Update the statistics panel."""
        total = self.batch_queue.total_items
        pending = self.batch_queue.pending_count
        completed = self.batch_queue.completed_count
        cards = self.batch_queue.total_cards_created

        self.total_label.setText(f"{IconProvider.get_icon('library')} {total} total series")

        self.pending_label.setText(f"{IconProvider.get_icon('progress')} {pending} pending")

        self.completed_label.setText(f"{IconProvider.get_icon('complete')} {completed} completed")

        self.cards_label.setText(f"{IconProvider.get_icon('card')} {cards} cards created")

    def _update_empty_state(self):
        """Update the empty state label visibility."""
        is_empty = self.batch_queue.total_items == 0
        self.empty_label.setVisible(is_empty)

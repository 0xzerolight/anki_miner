"""Multi-series queue panel for batch processing.

Under D28 this is a list you manipulate, not a stack of cards: rows highlight on
click, shift-click takes a range, ``Ctrl+A`` selects all, ``Delete`` removes the
selection, dragging or ``Alt+Up/Down`` reorders, and the shared
:class:`~anki_miner.gui.widgets.queue_controls_bar.QueueControlsBar` supplies the
filter chips, search, counter and selection actions the two list queues already
had. The same bar carries the D29-A lock badge and the two boundary controls
while a run owns the queue.

The queue *model* is not rebuilt for any of that. Rows bind to persistent
:class:`~anki_miner.models.batch_queue.QueueItem` identities, because each one
carries the episode receipts (``committed_pair_keys``) that stop a retry
re-mining pairs already in Anki. A row whose folders are not both set yet stays
unbound; it acquires its item the moment they validate, and an edit updates that
same object.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
)

from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.keyboard_shortcuts import (
    disown_default_buttons,
    primary_action_shortcut,
    scoped_shortcut,
)
from anki_miner.gui.widgets.base import configure_card_layout, field_label_width
from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton, SectionHeader
from anki_miner.gui.widgets.queue_controls_bar import QueueControlsBar
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget
from anki_miner.models.batch_queue import BatchQueue, QueueItem, QueueItemStatus
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

#: Text lines the empty-looking list still reserves when the filter or search
#: hides every row. Measured in lines, not pixels, so it holds at 1.5x text.
_VISIBLE_QUEUE_ROWS = 6

#: Card rows the list guarantees visible before it scrolls internally. A batch
#: queue row is a multi-line QueueItemWidget card, not a text line, so the
#: minimum is measured in whole cards: anything less clips a card's Edit/Remove
#: footer once a short window compresses the list to its minimum.
_VISIBLE_QUEUE_CARDS = 3

#: Row status -> filter chip. The same four words the row badge prints, so the
#: Failed chip selects exactly the rows reading "Failed".
_STATUS_BUCKETS = {
    "pending": "ready",
    "processing": "running",
    "error": "failed",
    "complete": "complete",
}


class QueuePanel(QFrame):
    """Multi-series queue management panel.

    Signals:
        process_requested: Emitted when user wants to process queue
        empty_changed: Emitted with whether the queue is now empty. The panel
            hides its own list when it is; the page hosting the panel uses this
            to swap in whatever else takes that height.
    """

    process_requested = pyqtSignal()
    empty_changed = pyqtSignal(bool)

    def __init__(self, parent=None, queue: BatchQueue | None = None):
        """Initialize the queue panel.

        Args:
            parent: Optional parent widget
            queue: The persistent model the rows bind to. The tab passes its own
                so removal, reorder and edits mutate one queue rather than the
                panel keeping a second, divergent copy.
        """
        super().__init__(parent)
        self.setObjectName("card")
        self.queue = queue if queue is not None else BatchQueue()
        self.queue_item_widgets: list[QueueItemWidget] = []
        self._list_items: dict[int, QListWidgetItem] = {}
        self._items: dict[int, QueueItem] = {}
        self._filter = "all"
        self._search = ""
        self._locked = False
        self._suppress_row_sync = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        configure_card_layout(layout)

        header = SectionHeader(title=self.tr("Multi-Series Queue"), action_text=self.tr("Add Series"))
        header.action_clicked.connect(self._add_series)
        layout.addWidget(header)

        self.queue_stats_label = QLabel()
        self.queue_stats_label.setObjectName("queue-stats")
        stats_font = QFont()
        stats_font.setPixelSize(FONT_SIZES.body_sm)
        stats_font.setWeight(QFont.Weight.Medium)
        self.queue_stats_label.setFont(stats_font)
        self.queue_stats_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.queue_stats_label)

        self.queue_controls = QueueControlsBar()
        # Batch-only amendment to the shared bar's Run tooltip: this is the one
        # queue where selecting a finished row and running it mines it again, so
        # the YouTube and Audiobook bars must keep the plain wording.
        self.queue_controls.run_button.setToolTip(
            self.tr("Mine the selected rows, in list order. A completed row is mined again from scratch.")
        )
        layout.addWidget(self.queue_controls)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("queue-list")
        layout.addWidget(self.list_widget)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)

        self.process_queue_button = ModernButton(self.tr("Process Queue"), variant="primary")
        self.process_queue_button.clicked.connect(self.process_requested.emit)
        self.process_queue_button.setToolTip(self.tr("Process all series in queue"))
        button_layout.addWidget(self.process_queue_button)

        self.clear_button = ModernButton(self.tr("Clear All"), variant="ghost")
        self.clear_button.clicked.connect(self._clear_queue)
        self.clear_button.setToolTip(self.tr("Remove all items from queue"))
        button_layout.addWidget(self.clear_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.setLayout(layout)

        self._wire_interaction()
        self._update_stats()

    def _wire_interaction(self) -> None:
        """Turn the plain list into a manipulable one (D28).

        Native list input owns selection and drag; everything here either
        mirrors that into the embedded row widgets -- ``setItemWidget`` puts an
        opaque widget over the item, so the row has to be told -- or supplies a
        verb Qt has no opinion about.
        """
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._update_list_min_height()
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)

        model = self.list_widget.model()
        if model is not None:
            model.rowsMoved.connect(self._on_rows_moved)

        self.queue_controls.filter_changed.connect(self._on_filter_changed)
        self.queue_controls.search_changed.connect(self._on_search_changed)
        self.queue_controls.run_selected.connect(self.process_requested.emit)
        self.queue_controls.retry_selected.connect(self._retry_selected)
        self.queue_controls.remove_selected.connect(self._remove_selected)

        # Scoped to the list: Delete and the Alt arrows must not fire from the
        # folder pickers or the offset spinbox on the same screen.
        widget_only = Qt.ShortcutContext.WidgetShortcut
        scoped_shortcut(self.list_widget, QKeySequence(Qt.Key.Key_Delete), self._remove_selected, context=widget_only)
        scoped_shortcut(self.list_widget, QKeySequence("Alt+Up"), lambda: self._move_selection(-1), context=widget_only)
        scoped_shortcut(
            self.list_widget, QKeySequence("Alt+Down"), lambda: self._move_selection(1), context=widget_only
        )

    def _update_list_min_height(self) -> None:
        """Size the list's minimum in whole visible cards, not text lines.

        Sums the size hints of the first ``_VISIBLE_QUEUE_CARDS`` rows the
        filter and search leave visible — the same hints ``register_widget``
        and ``_resize_row`` maintain from each card's real ``sizeHint()``, so
        collapsed rows count short and expanded rows tall. A text-line metric
        here held less than one card and clipped its footer buttons whenever a
        short window compressed the list to its minimum. Beyond the cap the
        list scrolls internally; the Expanding size policy still grows it
        further when the window has height to spare.
        """
        hints = [
            item.sizeHint().height()
            for widget in self.queue_item_widgets
            if (item := self._list_items.get(id(widget))) is not None and not item.isHidden()
        ]
        if hints:
            min_h = sum(hints[:_VISIBLE_QUEUE_CARDS]) + 2 * self.list_widget.frameWidth()
        else:
            # Filter/search hid every row: reserve a few text lines so the
            # still-visible list doesn't collapse to nothing. (An empty queue
            # hides the list entirely; see _update_stats.)
            min_h = _VISIBLE_QUEUE_ROWS * metric_row_height(self.list_widget)
        self.list_widget.setMinimumHeight(min_h)

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _add_series(self) -> None:
        """Add a new series row to the queue."""
        if self._locked:
            return
        # Instantiated rather than QInputDialog.getText: the static helper leaves
        # OK as the default button, so Return commits the dialog — and Return is
        # also how a Japanese input method commits a composition, which makes a
        # kana series name impossible to type (D49). Ctrl+Enter confirms instead.
        prompt = QInputDialog(self)
        prompt.setWindowTitle(self.tr("Add Series"))
        prompt.setLabelText(tr_format(self.tr("Enter a name for series #%1:"), len(self.queue_item_widgets) + 1))
        prompt.setTextValue(tr_format(self.tr("Series %1"), len(self.queue_item_widgets) + 1))
        disown_default_buttons(prompt)
        primary_action_shortcut(prompt, prompt.accept)
        if prompt.exec() != QDialog.DialogCode.Accepted:
            return
        name = prompt.textValue()
        if not name.strip():
            return

        widget = QueueItemWidget(display_name=name, parent=self.list_widget)
        widget.removed.connect(lambda: self._remove_item(widget))
        widget.edited.connect(lambda: self._edit_item(widget))
        self.register_widget(widget)

    def register_widget(self, widget: QueueItemWidget) -> None:
        """Put ``widget`` on the list and bind it if its folders already validate.

        The single entry point for a new row, so nothing can end up on the list
        without a list item and a filter state.
        """
        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(list_item)
        self.list_widget.setItemWidget(list_item, widget)
        # The row expands and collapses; the list item does not learn that on
        # its own, so a stale hint would clip the details it just opened.
        widget.size_changed.connect(lambda w=widget: self._resize_row(w))

        self.queue_item_widgets.append(widget)
        self._list_items[id(widget)] = list_item
        self._bind_widget(widget)

        list_item.setHidden(not self._row_visible(widget))
        self._update_stats()
        self._update_list_min_height()

    def _resize_row(self, widget: QueueItemWidget) -> None:
        """Re-hint the list item after its row changed height."""
        list_item = self._list_items.get(id(widget))
        if list_item is not None:
            list_item.setSizeHint(widget.sizeHint())
        self._update_list_min_height()

    def _bind_widget(self, widget: QueueItemWidget) -> QueueItem | None:
        """Give ``widget`` its persistent queue item once both folders validate.

        An incomplete row stays unbound rather than acquiring a placeholder item:
        a ``QueueItem`` is the thing a run and its receipts are addressed by, and
        one that names no folders would be a row the queue counts but can never
        mine.
        """
        video, subtitle = widget.get_folders()
        if video is None or subtitle is None:
            return None

        item = self._items.get(id(widget))
        if item is None:
            item = self.queue.add_item(video, subtitle, widget.display_name, widget.subtitle_offset)
            self._items[id(widget)] = item
            widget.item_id = item.id
            return item

        # Edited: same identity, new inputs. The receipts describe episodes that
        # are no longer this row's, so they go with the folders that produced them.
        if (item.video_folder, item.subtitle_folder) != (video, subtitle):
            item.video_folder = video
            item.subtitle_folder = subtitle
            self.queue.reset_run_history(item)
            widget.set_status("pending")
        item.display_name = widget.display_name
        item.subtitle_offset = widget.subtitle_offset
        return item

    def _remove_item(self, widget: QueueItemWidget) -> None:
        """Remove a queue item widget, its row and its model item.

        Args:
            widget: Widget to remove
        """
        if self._locked:
            return
        if widget not in self.queue_item_widgets:
            return
        self.queue_item_widgets.remove(widget)
        item = self._items.pop(id(widget), None)
        if item is not None:
            self.queue.remove(item)
        list_item = self._list_items.pop(id(widget), None)
        if list_item is not None:
            row = self.list_widget.row(list_item)
            if row >= 0:
                # takeItem deletes the QListWidgetItem; Qt destroys the embedded
                # widget alongside it.
                self.list_widget.takeItem(row)
        self._update_stats()
        self._update_list_min_height()

    def _edit_item(self, widget: QueueItemWidget) -> None:
        """Edit a queue item's folders and subtitle offset.

        Args:
            widget: Widget to edit
        """
        if self._locked:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr_format(self.tr("Edit: %1"), widget.display_name))
        dialog.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Shared label-column width so every labeled row lines up.
        label_w = field_label_width(self.tr("Video Folder:"), self.tr("Subtitle Folder:"), self.tr("Subtitle Offset:"))

        video_selector = FileSelector(
            label=self.tr("Video Folder:"),
            file_mode=False,
            label_width=label_w,
            history_key="video.batch.inputs",
        )
        current_video, current_subtitle = widget.get_folders()
        if current_video:
            video_selector.set_path(str(current_video))
        layout.addWidget(video_selector)

        subtitle_selector = FileSelector(
            label=self.tr("Subtitle Folder:"),
            file_mode=False,
            label_width=label_w,
            history_key="video.batch.inputs",
        )
        if current_subtitle:
            subtitle_selector.set_path(str(current_subtitle))
        layout.addWidget(subtitle_selector)

        folder_error = QLabel(self.tr("Choose existing video and subtitle folders."))
        folder_error.setWordWrap(True)
        folder_error.hide()
        layout.addWidget(folder_error)

        offset_layout = QHBoxLayout()
        offset_label = QLabel(self.tr("Subtitle Offset:"))
        offset_label.setObjectName("field-label")
        offset_label.setFixedWidth(label_w)
        offset_spinbox = QDoubleSpinBox()
        offset_spinbox.setRange(SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX)
        offset_spinbox.setSingleStep(0.5)
        offset_spinbox.setValue(widget.subtitle_offset)
        offset_spinbox.setSuffix(self.tr(" seconds"))
        offset_spinbox.setToolTip(self.tr("Adjust subtitle timing (positive = later, negative = earlier)"))
        offset_layout.addWidget(offset_label)
        offset_layout.addWidget(offset_spinbox)
        offset_layout.addStretch()
        layout.addLayout(offset_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        def selected_folders() -> tuple[Path, Path] | None:
            video_path = video_selector.path_or_none()
            subtitle_path = subtitle_selector.path_or_none()
            if (
                video_path is None
                or subtitle_path is None
                or not video_selector.is_valid()
                or not subtitle_selector.is_valid()
            ):
                return None
            return Path(video_path), Path(subtitle_path)

        def accept_if_valid() -> None:
            if selected_folders() is None:
                folder_error.show()
                return
            folder_error.hide()
            dialog.accept()

        button_box.accepted.connect(accept_if_valid)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.setLayout(layout)
        # This dialog owns two path fields and a spin box, so Return must stay
        # available for text entry rather than confirming the dialog (D49).
        disown_default_buttons(dialog)
        primary_action_shortcut(dialog, accept_if_valid)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            folders = selected_folders()
            if folders is None:
                return
            video_folder, subtitle_folder = folders

            widget.set_folders(video_folder, subtitle_folder)

            from anki_miner.utils.file_pairing import FilePairMatcher

            try:
                pairs = FilePairMatcher.find_pairs_by_episode_number(video_folder, subtitle_folder)
                widget.set_episode_count(len(pairs))
            except Exception as e:
                logger.warning("Failed to count episodes for %s: %s", widget.display_name, e)

            widget.subtitle_offset = offset_spinbox.value()
            self._bind_widget(widget)
            self._apply_view()
            self._update_stats()

    def _clear_queue(self) -> None:
        """Clear all items from the queue."""
        if self._locked:
            return
        if not self.queue_item_widgets:
            QMessageBox.information(self, self.tr("Empty Queue"), self.tr("Queue is already empty."))
            return

        # A confirmation, not an error report: this is destructive and
        # irreversible, which is the one thing D24 still allows a modal for.
        reply = QMessageBox.question(
            self,
            self.tr("Clear Queue"),
            tr_format(self.tr("Remove all %1 series from the queue?"), len(self.queue_item_widgets)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.clear_all()

    def clear_all(self) -> None:
        """Remove every row without asking. The confirm lives with the button."""
        for widget in list(self.queue_item_widgets):
            self._remove_item(widget)
        self.queue.clear()
        self._update_stats()

    # ------------------------------------------------------------------
    # Selection, filter, search
    # ------------------------------------------------------------------

    def _widget_at(self, list_item: QListWidgetItem | None) -> QueueItemWidget | None:
        """Resolve a list row back to its embedded widget."""
        if list_item is None:
            return None
        for widget in self.queue_item_widgets:
            if self._list_items.get(id(widget)) is list_item:
                return widget
        return None

    def selected_widgets(self) -> list[QueueItemWidget]:
        """Selected, visible rows in the order the list shows them.

        Hidden rows are excluded rather than merely deselected on filter change:
        a selected action must never reach a row the user cannot see.
        """
        selected: list[QueueItemWidget] = []
        for row in range(self.list_widget.count()):
            list_item = self.list_widget.item(row)
            if list_item is None or list_item.isHidden() or not list_item.isSelected():
                continue
            widget = self._widget_at(list_item)
            if widget is not None:
                selected.append(widget)
        return selected

    def view_order(self) -> list[QueueItemWidget]:
        """Rows in the order the list currently shows them."""
        order: list[QueueItemWidget] = []
        for row in range(self.list_widget.count()):
            widget = self._widget_at(self.list_widget.item(row))
            if widget is not None:
                order.append(widget)
        return order

    def _on_selection_changed(self) -> None:
        """Mirror the view's selection into the rows and the action buttons."""
        selected = {id(w) for w in self.selected_widgets()}
        for widget in self.queue_item_widgets:
            widget.set_selected(id(widget) in selected)
        self._refresh_selection_actions()

    def _refresh_selection_actions(self) -> None:
        """Enable each selection verb only where it has something to act on.

        All three are off during a run: the worker mines a snapshot frozen at
        launch, so mutating the list underneath it would change what the counters
        and the receipt describe without changing what actually gets mined.
        """
        selected = self.selected_widgets()
        runnable = any(self._items.get(id(w)) is not None for w in selected)
        retryable = any(w.get_status() == "error" for w in selected)
        removable = any(w.get_status() != "processing" for w in selected)
        self.queue_controls.set_actions_enabled(
            run=runnable and not self._locked,
            retry=retryable and not self._locked,
            remove=removable and not self._locked,
        )

    def _on_filter_changed(self, key: str) -> None:
        """Adopt a filter chip and re-apply the view."""
        self._filter = key
        self._apply_view()

    def _on_search_changed(self, text: str) -> None:
        """Adopt the search text and re-apply the view."""
        self._search = text
        self._apply_view()

    def _row_visible(self, widget: QueueItemWidget) -> bool:
        """Whether ``widget`` survives both the active chip and the search text."""
        if self._filter != "all" and _STATUS_BUCKETS.get(widget.get_status(), "ready") != self._filter:
            return False
        needle = self._search.strip().casefold()
        return not needle or needle in widget.display_name.casefold()

    def _apply_view(self) -> None:
        """Hide the rows the filter and search exclude, and deselect them."""
        for widget in self.queue_item_widgets:
            list_item = self._list_items.get(id(widget))
            if list_item is None:
                continue
            visible = self._row_visible(widget)
            if not visible and list_item.isSelected():
                list_item.setSelected(False)
            list_item.setHidden(not visible)
        self._refresh_counts()
        self._refresh_selection_actions()
        self._update_list_min_height()

    def _refresh_counts(self) -> None:
        """Restate the queue's shape. Counts the queue, never the current view."""
        buckets = [_STATUS_BUCKETS.get(w.get_status(), "ready") for w in self.queue_item_widgets]
        self.queue_controls.set_counts(
            total=len(self.queue_item_widgets),
            ready=buckets.count("ready"),
            failed=buckets.count("failed"),
            complete=buckets.count("complete"),
        )

    def _remove_selected(self) -> None:
        """Drop the selected rows. A row being mined is left where it is."""
        for widget in self.selected_widgets():
            if widget.get_status() == "processing":
                continue
            self._remove_item(widget)
        self._apply_view()

    def _retry_selected(self) -> None:
        """Return the selected failed rows to pending, keeping their receipts.

        The episode receipts are deliberately preserved: a retry that re-mined
        pairs already in Anki would duplicate the user's cards, which is exactly
        what ``committed_pair_keys`` exists to prevent.
        """
        for widget in self.selected_widgets():
            if widget.get_status() != "error":
                continue
            widget.set_status("pending")
            item = self._items.get(id(widget))
            if item is not None:
                item.status = QueueItemStatus.PENDING
                item.error_message = ""
        self._apply_view()

    # ------------------------------------------------------------------
    # Reorder
    # ------------------------------------------------------------------

    def _move_selection(self, delta: int) -> None:
        """Move every selected row one place up (-1) or down (+1)."""
        if self._locked:
            return
        order = self.view_order()
        rows = sorted(order.index(w) for w in self.selected_widgets())
        if not rows:
            return
        if delta < 0:
            if rows[0] == 0:
                return
            for row in rows:
                order[row - 1], order[row] = order[row], order[row - 1]
        else:
            if rows[-1] == len(order) - 1:
                return
            for row in reversed(rows):
                order[row + 1], order[row] = order[row], order[row + 1]
        self._reorder_to(order)

    def _reorder_to(self, order: list[QueueItemWidget]) -> None:
        """Adopt ``order`` in the list widget and then in the queue model.

        Realised through ``QAbstractItemModel.moveRow`` rather than take/insert:
        a moved row keeps the widget that was set on it, where a taken item's
        widget is Qt's to destroy.
        """
        model = self.list_widget.model()
        if model is None:
            return
        selected = self.selected_widgets()
        self._suppress_row_sync = True
        try:
            root = self.list_widget.rootIndex()
            for target, widget in enumerate(order):
                list_item = self._list_items.get(id(widget))
                if list_item is None:
                    continue
                current = self.list_widget.row(list_item)
                if current != target:
                    model.moveRow(root, current, root, target)
        finally:
            self._suppress_row_sync = False
        self._sync_model_order()
        for widget in selected:
            list_item = self._list_items.get(id(widget))
            if list_item is not None:
                list_item.setSelected(True)

    def _on_rows_moved(self, *_args) -> None:
        """Adopt the order the user dragged the rows into."""
        if self._suppress_row_sync:
            return
        self._sync_model_order()

    def _sync_model_order(self) -> None:
        """Rebuild the queue's order from the visible row order.

        Only the bound rows are in the model, so the permutation is taken over
        those; an unbound row has no identity to place.
        """
        self.queue_item_widgets = self.view_order()
        bound = [self._items[id(w)] for w in self.queue_item_widgets if id(w) in self._items]
        if len(bound) == len(self.queue.get_all_items()):
            self.queue.reorder(bound)

    # ------------------------------------------------------------------
    # Run lock (D29-A)
    # ------------------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        """Freeze the queue for the duration of a run.

        Args:
            locked: Whether a run currently owns the queue.
        """
        self._locked = locked
        self.clear_button.setEnabled(not locked)
        self.queue_controls.set_running(locked)
        drag = QListWidget.DragDropMode.NoDragDrop if locked else QListWidget.DragDropMode.InternalMove
        self.list_widget.setDragDropMode(drag)
        self.list_widget.setDragEnabled(not locked)
        for widget in self.queue_item_widgets:
            widget.setEnabled(not locked)
        self._refresh_selection_actions()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def _update_stats(self) -> None:
        """Update the queue statistics display."""
        series_count = len(self.queue_item_widgets)

        total_episodes = 0
        total_cards = 0
        for widget in self.queue_item_widgets:
            total_episodes += widget.get_episode_count()
            total_cards += widget.get_cards_created()

        if series_count == 0:
            text = self.tr("Queue is empty")
        elif total_cards > 0:
            text = tr_format(
                self.tr("%1 series - %2 episodes - %3 cards created"), series_count, total_episodes, total_cards
            )
        else:
            text = tr_format(self.tr("%1 series - %2 episodes - Ready to process"), series_count, total_episodes)

        self.queue_stats_label.setText(text)
        self._refresh_counts()

        # An empty queue reserved six rows of nothing above a line saying the
        # queue was empty. The list goes away instead and the line stands on its
        # own. The panel owns the list, so it hides it; the page owns whatever
        # takes the height that frees up, so it is told rather than reached into.
        is_empty = series_count == 0
        self.list_widget.setVisible(not is_empty)
        self.empty_changed.emit(is_empty)

    # === Public API ===

    def add_series_external(self) -> None:
        """Add a series (for external shortcut binding)."""
        self._add_series()

    def restore_item(
        self,
        *,
        item_id: str,
        display_name: str,
        video_folder: Path,
        subtitle_folder: Path,
        subtitle_offset: float,
        status: str,
        cards_created: int,
        retry_count: int,
        error_message: str,
    ) -> QueueItem | None:
        """Re-add one row from a recovery snapshot, keeping its identity (D16-C).

        Same construction path as an ordinary Add, then the stored id is
        re-attached: the id is how the worker and the row find each other, so a
        restored row that took a fresh one would be a different row wearing the
        same name.

        Returns:
            The bound ``QueueItem``, or ``None`` when the row could not be bound
            (its folders no longer validate).
        """
        widget = QueueItemWidget(display_name=display_name, parent=self.list_widget)
        widget.removed.connect(lambda: self._remove_item(widget))
        widget.edited.connect(lambda: self._edit_item(widget))
        widget.set_folders(video_folder, subtitle_folder)
        widget.subtitle_offset = subtitle_offset
        self.register_widget(widget)

        item = self._items.get(id(widget))
        if item is None:
            return None
        item.id = item_id
        widget.item_id = item_id
        item.cards_created = cards_created
        item.retry_count = retry_count
        item.error_message = error_message
        item.status = QueueItemStatus(status)
        widget.set_cards_created(cards_created)
        widget.set_status("complete" if item.status is QueueItemStatus.COMPLETED else status)
        self._update_stats()
        return item

    def has_only_completed_rows(self) -> bool:
        """Whether every row that could run has already finished.

        Read off the badges, like the chip counts are: it is what the user can
        see, and it is the one case where a run with nothing to do is not a
        broken queue but a finished one.
        """
        bound = [w for w in self.queue_item_widgets if id(w) in self._items]
        return bool(bound) and all(w.get_status() == "complete" for w in bound)

    def runnable_items(self) -> list[QueueItem]:
        """The bound, runnable rows a Process Queue click should mine.

        The selection when there is one, in the order the list shows it;
        otherwise every runnable row. A run therefore mines exactly what the user
        can see it is about to mine (D28, D29-A).

        Selecting a row is the user saying "mine this one", so a selected row
        that already finished is reset and mined again -- that is the only way
        back after a settings change. It is a full reset, receipts included: the
        worker skips every pair already in ``committed_pair_keys``, so keeping
        them would make the re-run a silent no-op that instantly reports
        Complete with 0 cards. Nothing is duplicated by it, because the
        known-words filter and ``allow_duplicate_cards`` (False by default) are
        what actually stop a second card for a word already in Anki. Without a
        selection the sweep is unchanged: a finished row is left finished, so
        Process Queue never silently re-mines the whole cohort.
        """
        selection = self.selected_widgets()
        chosen = selection or self.view_order()
        items: list[QueueItem] = []
        rerun = False
        for widget in chosen:
            item = self._items.get(id(widget))
            if item is None or widget.get_status() == "processing":
                continue
            if item.status in (QueueItemStatus.PENDING, QueueItemStatus.ERROR):
                item.status = QueueItemStatus.PENDING
                items.append(item)
            elif selection and item.status is QueueItemStatus.COMPLETED:
                self.queue.reset_run_history(item)
                # The badge goes with it: a row about to be mined must not still
                # read Complete, and the chip counts are taken from the badges.
                widget.set_status("pending")
                widget.set_cards_created(0)
                items.append(item)
                rerun = True
        if rerun:
            self._update_stats()
            self._apply_view()
        return items

    def get_valid_pairs(self) -> list:
        """Get all valid folder pairs for processing.

        Returns:
            List of tuples:
            (video_folder, subtitle_folder, display_name, subtitle_offset, widget)
        """
        valid_pairs = []
        for widget in self.queue_item_widgets:
            video, subtitle = widget.get_folders()
            if video and subtitle and video.exists() and subtitle.exists():
                valid_pairs.append((video, subtitle, widget.display_name, widget.subtitle_offset, widget))
        return valid_pairs

    def get_incomplete_items(self) -> list:
        """Get items with missing or invalid folders.

        Returns:
            List of (widget, issue_type) where issue_type is 'incomplete' or 'invalid'
        """
        incomplete = []
        for widget in self.queue_item_widgets:
            video, subtitle = widget.get_folders()
            if video and subtitle:
                if not video.exists() or not subtitle.exists():
                    incomplete.append((widget, "invalid"))
            else:
                incomplete.append((widget, "incomplete"))
        return incomplete

    def set_item_status(self, item_id: str, status: str) -> None:
        """Set status for an item by its stable id.

        Keyed by ``item_id`` (not display name): two rows can share a series
        name, and the worker addresses a specific QueueItem by id (T-30).

        Args:
            item_id: Item id (QueueItem.id stamped onto the widget)
            status: New status ('pending', 'processing', 'complete', 'error')
        """
        for widget in self.queue_item_widgets:
            if widget.item_id == item_id:
                widget.set_status(status)
                break
        self._apply_view()

    def set_processing_item_complete(self, item_id: str, cards_created: int) -> None:
        """Mark a specific item complete and record its card count.

        Keyed by ``item_id`` rather than "first row that is processing": with
        duplicate series names, multiple rows are processing at once and the
        first-match heuristic landed counts on the wrong row (T-30).

        Args:
            item_id: Item id (QueueItem.id stamped onto the widget)
            cards_created: Number of cards created
        """
        for widget in self.queue_item_widgets:
            if widget.item_id == item_id:
                widget.set_status("complete")
                widget.set_cards_created(cards_created)
                break
        self._update_stats()
        self._apply_view()

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

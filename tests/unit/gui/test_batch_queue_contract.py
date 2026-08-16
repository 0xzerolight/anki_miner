"""Batch gets the same queue contract as the list queues (D28, D29-A, D30).

W5-T8 gave YouTube and Audio a real list — selection, shift-range, ``Ctrl+A``,
Delete, drag reorder, filter chips, a counter and a bulk action bar. Batch kept
a stack of cards in a scroll area, and ran from a snapshot while those cards
stayed editable, so removing a row did not stop it creating that series' cards.

These tests pin the contract without pinning the model: ``BatchQueue`` and its
``QueueItem`` identities are the same objects throughout, because each one
carries the episode receipts that stop a retry re-mining pairs already in Anki.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QListWidget

from anki_miner.gui.widgets.panels.queue_panel import QueuePanel
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget
from anki_miner.models.batch_queue import BatchQueue, QueueItemStatus


@pytest.fixture
def panel(qapp, qtbot):
    p = QueuePanel(queue=BatchQueue())
    qtbot.addWidget(p)
    yield p
    p.deleteLater()


def _add(panel, name, tmp_path, *, bound=True):
    """Add a row, giving it real folders when it should bind to a queue item."""
    widget = QueueItemWidget(display_name=name, parent=panel.list_widget)
    if bound:
        video = tmp_path / f"{name}-video"
        subs = tmp_path / f"{name}-subs"
        video.mkdir()
        subs.mkdir()
        widget.set_folders(video, subs)
    panel.register_widget(widget)
    return widget


# ---------------------------------------------------------------------------
# The list itself
# ---------------------------------------------------------------------------


def test_the_queue_is_a_selectable_reorderable_list(panel, tmp_path):
    _add(panel, "a", tmp_path)
    _add(panel, "b", tmp_path)

    assert panel.list_widget.selectionMode() is QListWidget.SelectionMode.ExtendedSelection
    assert panel.list_widget.dragDropMode() is QListWidget.DragDropMode.InternalMove
    assert panel.list_widget.count() == 2


def test_selection_actions_operate_on_the_selection_only(panel, tmp_path):
    first = _add(panel, "a", tmp_path)
    _add(panel, "b", tmp_path)

    panel.list_widget.item(0).setSelected(True)
    assert panel.selected_widgets() == [first]

    panel._remove_selected()

    assert [w.display_name for w in panel.queue_item_widgets] == ["b"]
    assert [i.display_name for i in panel.queue.get_all_items()] == ["b"]


def test_hidden_rows_are_never_reached_by_a_selection_action(panel, tmp_path):
    first = _add(panel, "keep", tmp_path)
    _add(panel, "other", tmp_path)
    panel.list_widget.item(0).setSelected(True)
    panel.list_widget.item(1).setSelected(True)

    panel._on_search_changed("keep")

    assert panel.selected_widgets() == [first]


def test_reorder_moves_the_model_not_just_the_view(panel, tmp_path):
    first = _add(panel, "a", tmp_path)
    second = _add(panel, "b", tmp_path)
    panel.list_widget.item(1).setSelected(True)

    panel._move_selection(-1)

    assert [w.display_name for w in panel.view_order()] == ["b", "a"]
    assert panel.queue.get_all_items() == [
        panel._items[id(second)],
        panel._items[id(first)],
    ]


def test_counter_reports_the_queue_not_the_view(panel, tmp_path):
    _add(panel, "a", tmp_path)
    second = _add(panel, "b", tmp_path)
    second.set_status("error")
    panel._apply_view()

    panel._on_filter_changed("failed")

    assert panel.list_widget.item(0).isHidden()
    assert "2 queued" in panel.queue_controls.counter_label.text()
    assert "1 failed" in panel.queue_controls.counter_label.text()


# ---------------------------------------------------------------------------
# Identity: the model is reused, never rebuilt
# ---------------------------------------------------------------------------


def test_an_incomplete_row_stays_unbound(panel, tmp_path):
    _add(panel, "no-folders", tmp_path, bound=False)

    assert panel.queue.get_all_items() == []
    assert panel.runnable_items() == []


def test_a_bound_row_keeps_its_identity_across_runs(panel, tmp_path):
    widget = _add(panel, "a", tmp_path)
    item = panel._items[id(widget)]
    item.committed_pair_keys.add((tmp_path / "ep1.mkv", tmp_path / "ep1.ass"))

    first = panel.runnable_items()
    widget.set_status("error")
    item.status = QueueItemStatus.ERROR
    second = panel.runnable_items()

    assert first == [item]
    assert second == [item]
    # The receipts survive, which is what stops a retry duplicating cards.
    assert item.committed_pair_keys


def test_editing_the_folders_clears_that_row_s_receipts(panel, tmp_path):
    widget = _add(panel, "a", tmp_path)
    item = panel._items[id(widget)]
    item.committed_pair_keys.add((tmp_path / "ep1.mkv", tmp_path / "ep1.ass"))
    item.status = QueueItemStatus.COMPLETED

    new_video = tmp_path / "new-video"
    new_subs = tmp_path / "new-subs"
    new_video.mkdir()
    new_subs.mkdir()
    widget.set_folders(new_video, new_subs)
    panel._bind_widget(widget)

    assert panel._items[id(widget)] is item, "identity must survive an edit"
    assert item.committed_pair_keys == set()
    assert item.status is QueueItemStatus.PENDING


def test_runnable_items_prefers_the_selection_in_view_order(panel, tmp_path):
    _add(panel, "a", tmp_path)
    second = _add(panel, "b", tmp_path)
    panel.list_widget.item(1).setSelected(True)

    assert panel.runnable_items() == [panel._items[id(second)]]


# ---------------------------------------------------------------------------
# The lock (D29-A)
# ---------------------------------------------------------------------------


def test_locking_freezes_every_queue_verb(panel, tmp_path):
    widget = _add(panel, "a", tmp_path)
    panel.list_widget.item(0).setSelected(True)

    panel.set_locked(True)

    assert not panel.clear_button.isEnabled()
    assert not panel.queue_controls.remove_button.isEnabled()
    assert not panel.queue_controls.retry_button.isEnabled()
    assert panel.list_widget.dragDropMode() is QListWidget.DragDropMode.NoDragDrop
    assert panel.queue_controls.lock_label.text() == "Queue locked while processing."

    # And the verbs themselves refuse rather than merely looking disabled.
    panel._remove_selected()
    panel._move_selection(-1)
    panel._add_series()
    assert panel.queue_item_widgets == [widget]
    assert len(panel.queue.get_all_items()) == 1


def test_unlocking_restores_the_queue(panel, tmp_path):
    _add(panel, "a", tmp_path)

    panel.set_locked(True)
    panel.set_locked(False)

    assert panel.clear_button.isEnabled()
    assert panel.list_widget.dragDropMode() is QListWidget.DragDropMode.InternalMove
    assert panel.queue_controls.lock_label.isHidden()


def test_a_click_selects_the_row_instead_of_being_swallowed(panel, tmp_path, qtbot):
    """The row covers the list item, so a press it consumes never selects (D28)."""
    from PyQt6.QtCore import QPoint, Qt

    widget = _add(panel, "a", tmp_path)
    _add(panel, "b", tmp_path)
    panel.show()
    qtbot.waitExposed(panel)

    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))

    assert panel.selected_widgets() == [widget]


def test_double_click_still_expands_and_re_hints_the_row(panel, tmp_path):
    """Expanding must not be clipped by a size hint taken before it happened."""
    widget = _add(panel, "a", tmp_path)
    list_item = panel._list_items[id(widget)]

    widget.toggle_expanded()  # collapse
    collapsed = list_item.sizeHint().height()
    widget.toggle_expanded()  # expand again

    assert list_item.sizeHint().height() > collapsed


def test_a_worker_that_never_starts_does_not_strand_the_lock(qtbot, test_config):
    """D29-A locks the queue before the run begins, so the failure path must unlock.

    Without the rollback the panel stays frozen against a run that never
    existed, and there is no thread whose ``finished`` could ever release it.
    """
    from unittest.mock import MagicMock, patch

    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab

    tab = BatchProcessingTab(test_config, MagicMock(), MagicMock())
    qtbot.addWidget(tab)
    tab._show_cancel_state()
    assert not tab.queue_panel.clear_button.isEnabled()

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        side_effect=RuntimeError("no anki fields"),
    ):
        tab._start_queue_worker()

    assert tab.worker_thread is None
    assert tab.queue_panel.clear_button.isEnabled()
    assert not tab._is_processing
    tab.deleteLater()


# ---------------------------------------------------------------------------
# Re-running a completed row
# ---------------------------------------------------------------------------


def test_a_selected_complete_row_is_mined_again_from_scratch(panel, tmp_path):
    widget = _add(panel, "a", tmp_path)
    item = panel._items[id(widget)]
    item.status = QueueItemStatus.COMPLETED
    item.cards_created = 12
    item.committed_pair_keys.add((tmp_path / "ep1.mkv", tmp_path / "ep1.ass"))
    widget.set_status("complete")
    widget.set_cards_created(12)
    panel.list_widget.item(0).setSelected(True)

    assert panel.runnable_items() == [item]
    # The receipts must go, or the worker skips every pair and the re-run is a
    # no-op that instantly reports Complete with 0 cards.
    assert item.committed_pair_keys == set()
    assert item.status is QueueItemStatus.PENDING
    assert item.cards_created == 0
    assert widget.get_status() == "pending"


def test_an_unselected_complete_row_is_left_alone(panel, tmp_path):
    widget = _add(panel, "done", tmp_path)
    pending = _add(panel, "todo", tmp_path)
    done_item = panel._items[id(widget)]
    done_item.status = QueueItemStatus.COMPLETED
    done_item.committed_pair_keys.add((tmp_path / "ep1.mkv", tmp_path / "ep1.ass"))
    widget.set_status("complete")

    # No selection: Process Queue still means "mine what is not done yet".
    assert panel.runnable_items() == [panel._items[id(pending)]]
    assert done_item.status is QueueItemStatus.COMPLETED
    assert done_item.committed_pair_keys


def test_a_selected_failed_row_still_keeps_its_receipts(panel, tmp_path):
    widget = _add(panel, "a", tmp_path)
    item = panel._items[id(widget)]
    item.status = QueueItemStatus.ERROR
    item.committed_pair_keys.add((tmp_path / "ep1.mkv", tmp_path / "ep1.ass"))
    widget.set_status("error")
    panel.list_widget.item(0).setSelected(True)

    assert panel.runnable_items() == [item]
    assert item.status is QueueItemStatus.PENDING
    assert item.committed_pair_keys, "a retry must not re-mine pairs already in Anki"


def test_has_only_completed_rows_is_true_when_every_bound_row_is_done(panel, tmp_path):
    first = _add(panel, "a", tmp_path)
    second = _add(panel, "b", tmp_path)
    first.set_status("complete")
    second.set_status("complete")

    assert panel.has_only_completed_rows() is True

    second.set_status("pending")
    assert panel.has_only_completed_rows() is False


def test_has_only_completed_rows_is_false_for_an_empty_queue(panel, tmp_path):
    assert panel.has_only_completed_rows() is False
    _add(panel, "unbound", tmp_path, bound=False)
    assert panel.has_only_completed_rows() is False

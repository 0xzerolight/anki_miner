"""The list queues are manipulable lists (decisions D28, D31).

The app exists to mine in batches, and until now the YouTube and Audio queues
set ``NoSelection``: a user could add rows and clear the lot, and nothing in
between. These tests pin the missing verbs -- select, filter, search, count,
reorder, and act on the selection -- on both tabs that share
``_ListQueueMiningTabBase``.

Reorder is locked while a run is consuming its frozen snapshot: the worker
resolves its ``idx`` signals against ``_run_items``, and letting the user
shuffle the queue underneath it is how a finished item's result lands on
another row.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget

from anki_miner.gui.controllers.task_registry import TaskRegistry
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.models.youtube import VideoInfo
from anki_miner.models.youtube_queue import YouTubeItemStatus

# ---------------------------------------------------------------------------
# Both tabs, one contract
# ---------------------------------------------------------------------------


@pytest.fixture
def audiobook_tab(qtbot, test_config):
    with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = AudiobookTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


@pytest.fixture
def youtube_tab(qtbot, test_config):
    with patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(
            config=test_config,
            processor=MagicMock(),
            fetcher=MagicMock(),
            presenter=MagicMock(),
        )
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


def _add_audiobook(tab, tmp_path: Path, stem: str):
    audio = tmp_path / f"{stem}.m4b"
    sub = tmp_path / f"{stem}.srt"
    audio.touch()
    sub.touch()
    item = tab._queue.add(audio, sub)
    tab._render_new_item(item)
    tab._recompute_buttons()
    return item


def _add_youtube(tab, title: str):
    item = tab._queue.add(f"https://youtu.be/{title}")
    item.status = YouTubeItemStatus.READY
    item.video_info = VideoInfo(
        video_id=title,
        title=title,
        duration_s=120,
        has_manual_ja_subs=True,
        has_auto_ja_subs=False,
        is_live=False,
        is_age_restricted=False,
    )
    tab._render_new_item(item)
    tab._recompute_buttons()
    return item


@pytest.fixture(params=["audiobook", "youtube"])
def queue(request, audiobook_tab, youtube_tab, tmp_path):
    """A tab plus an ``add(name)`` helper, once per list-queue tab."""
    if request.param == "audiobook":
        return audiobook_tab, lambda name: _add_audiobook(audiobook_tab, tmp_path, name)
    return youtube_tab, lambda name: _add_youtube(youtube_tab, name)


def _rows(tab) -> list:
    """Queue items in the order the list widget shows them."""
    reverse = {id(li): item for item, li in tab._list_items.items()}
    return [reverse[id(tab.list_widget.item(i))] for i in range(tab.list_widget.count())]


def _select(tab, items) -> None:
    for item in items:
        tab._list_items[item].setSelected(True)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_list_allows_a_multi_row_selection(queue) -> None:
    tab, add = queue
    assert tab.list_widget.selectionMode() == QListWidget.SelectionMode.ExtendedSelection


def test_selecting_rows_marks_their_widgets(queue) -> None:
    tab, add = queue
    first, second = add("a"), add("b")

    _select(tab, [first])

    assert tab._row_widgets[first].is_selected() is True
    assert tab._row_widgets[second].is_selected() is False


def test_deselecting_clears_the_row_marking(queue) -> None:
    tab, add = queue
    item = add("a")
    _select(tab, [item])

    tab._list_items[item].setSelected(False)

    assert tab._row_widgets[item].is_selected() is False


def test_ctrl_a_selects_every_visible_row(queue, qtbot) -> None:
    tab, add = queue
    shown, hidden = add("keep"), add("other")
    tab._on_queue_search_changed("keep")

    tab.list_widget.setFocus()
    qtbot.keyClick(tab.list_widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)

    assert tab._selected_items() == [shown]
    assert tab._list_items[hidden].isSelected() is False


def test_selected_items_are_returned_in_list_order(queue) -> None:
    tab, add = queue
    first, _second, third = add("a"), add("b"), add("c")

    _select(tab, [third, first])

    assert tab._selected_items() == [first, third]


# ---------------------------------------------------------------------------
# Filters and search
# ---------------------------------------------------------------------------


def test_filter_hides_rows_outside_the_bucket(queue) -> None:
    tab, add = queue
    ready, done = add("a"), add("b")
    done.status = tab._status_completed

    tab._on_queue_filter_changed("ready")

    assert tab._list_items[ready].isHidden() is False
    assert tab._list_items[done].isHidden() is True


def test_all_filter_shows_everything_again(queue) -> None:
    tab, add = queue
    ready, done = add("a"), add("b")
    done.status = tab._status_completed
    tab._on_queue_filter_changed("complete")

    tab._on_queue_filter_changed("all")

    assert tab._list_items[ready].isHidden() is False
    assert tab._list_items[done].isHidden() is False


def test_search_matches_the_row_text(queue) -> None:
    tab, add = queue
    wanted, other = add("alpha"), add("beta")

    tab._on_queue_search_changed("alph")

    assert tab._list_items[wanted].isHidden() is False
    assert tab._list_items[other].isHidden() is True


def test_search_is_case_insensitive(queue) -> None:
    tab, add = queue
    wanted = add("Alpha")

    tab._on_queue_search_changed("ALPHA")

    assert tab._list_items[wanted].isHidden() is False


def test_hiding_a_row_drops_it_from_the_selection(queue) -> None:
    """Selected actions must never touch a row the user cannot see."""
    tab, add = queue
    first, second = add("alpha"), add("beta")
    _select(tab, [first, second])

    tab._on_queue_search_changed("alpha")

    assert tab._selected_items() == [first]
    assert tab._list_items[second].isSelected() is False


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------


def test_counter_reports_the_whole_queue(queue) -> None:
    tab, add = queue
    add("a")
    failed, done = add("b"), add("c")
    failed.status = tab._status_error
    done.status = tab._status_completed
    tab._refresh_queue_counts()

    assert tab.queue_controls.counter_label.text() == "3 queued · 1 ready · 1 failed · 1 complete"


def test_counter_ignores_the_active_filter(queue) -> None:
    """The counter states the queue, not the current view of it."""
    tab, add = queue
    add("a")
    add("b").status = tab._status_completed

    tab._on_queue_filter_changed("ready")

    assert tab.queue_controls.counter_label.text().startswith("2 queued")


# ---------------------------------------------------------------------------
# Selection actions
# ---------------------------------------------------------------------------


def test_actions_are_disabled_without_a_selection(queue) -> None:
    tab, add = queue
    add("a")

    assert tab.queue_controls.run_button.isEnabled() is False
    assert tab.queue_controls.retry_button.isEnabled() is False
    assert tab.queue_controls.remove_button.isEnabled() is False


def test_run_enables_only_for_a_minable_selection(queue) -> None:
    tab, add = queue
    ready, done = add("a"), add("b")
    done.status = tab._status_completed
    tab._refresh_row(done)

    _select(tab, [done])
    assert tab.queue_controls.run_button.isEnabled() is False

    _select(tab, [ready])
    assert tab.queue_controls.run_button.isEnabled() is True


def test_retry_enables_only_for_a_failed_selection(queue) -> None:
    tab, add = queue
    ready, failed = add("a"), add("b")
    failed.status = tab._status_error
    tab._refresh_row(failed)

    _select(tab, [ready])
    assert tab.queue_controls.retry_button.isEnabled() is False

    tab.list_widget.clearSelection()
    _select(tab, [failed])
    assert tab.queue_controls.retry_button.isEnabled() is True


def test_remove_selected_drops_only_the_selected_rows(queue) -> None:
    tab, add = queue
    first, second, third = add("a"), add("b"), add("c")
    _select(tab, [first, third])

    tab._on_remove_selected()

    assert tab._queue.all_items() == [second]
    assert tab.list_widget.count() == 1


def test_remove_selected_leaves_the_running_row_alone(queue) -> None:
    tab, add = queue
    running = add("a")
    running.status = tab._status_processing
    _select(tab, [running])

    tab._on_remove_selected()

    assert tab._queue.all_items() == [running]


def test_run_selected_mines_only_the_selection(queue) -> None:
    tab, add = queue
    first, second = add("a"), add("b")
    _select(tab, [second])

    tab._on_run_selected()

    assert tab._run_items == [second]
    assert first.status == tab._status_ready


def test_retry_selected_returns_failed_rows_to_ready(queue) -> None:
    tab, add = queue
    failed = add("a")
    failed.status = tab._status_error
    failed.error_message = "boom"
    tab._refresh_row(failed)
    _select(tab, [failed])

    tab._on_retry_selected()

    assert failed.status == tab._status_ready
    assert failed.error_message is None


def test_retry_selected_starts_a_run_for_the_reset_rows(queue) -> None:
    tab, add = queue
    failed = add("a")
    failed.status = tab._status_error
    tab._refresh_row(failed)
    _select(tab, [failed])

    tab._on_retry_selected()

    assert tab._run_items == [failed]


# ---------------------------------------------------------------------------
# Reorder
# ---------------------------------------------------------------------------


def test_list_accepts_internal_drag_reorder(queue) -> None:
    tab, add = queue
    assert tab.list_widget.dragDropMode() == QListWidget.DragDropMode.InternalMove


def test_alt_down_moves_the_selection_one_place(queue) -> None:
    tab, add = queue
    first, second, third = add("a"), add("b"), add("c")
    _select(tab, [first])

    tab._move_selection(1)

    assert _rows(tab) == [second, first, third]
    assert tab._queue.all_items() == [second, first, third]


def test_alt_up_moves_the_selection_one_place(queue) -> None:
    tab, add = queue
    first, second, third = add("a"), add("b"), add("c")
    _select(tab, [third])

    tab._move_selection(-1)

    assert _rows(tab) == [first, third, second]
    assert tab._queue.all_items() == [first, third, second]


def test_moving_past_the_end_is_a_no_op(queue) -> None:
    tab, add = queue
    first, second = add("a"), add("b")
    _select(tab, [second])

    tab._move_selection(1)

    assert tab._queue.all_items() == [first, second]


def test_reordering_keeps_the_selection(queue) -> None:
    tab, add = queue
    first, _second = add("a"), add("b")
    _select(tab, [first])

    tab._move_selection(1)

    assert tab._selected_items() == [first]


def test_a_user_drag_resyncs_the_model_from_the_view(queue) -> None:
    """Qt moves the rows; the queue must adopt what the user sees."""
    tab, add = queue
    first, second, third = add("a"), add("b"), add("c")

    tab.list_widget.model().moveRow(
        tab.list_widget.rootIndex(),
        2,
        tab.list_widget.rootIndex(),
        0,
    )

    assert tab._queue.all_items() == [third, first, second]


# ---------------------------------------------------------------------------
# Reorder lock
# ---------------------------------------------------------------------------


def test_reorder_is_locked_while_a_run_is_active(queue) -> None:
    tab, add = queue
    add("a")
    add("b")
    tab._on_mine_clicked()

    assert tab.list_widget.dragDropMode() == QListWidget.DragDropMode.NoDragDrop
    assert tab.list_widget.dragEnabled() is False


def test_alt_move_is_a_no_op_while_a_run_is_active(queue) -> None:
    tab, add = queue
    first, second = add("a"), add("b")
    _select(tab, [second])
    tab._on_mine_clicked()

    tab._move_selection(-1)

    assert tab._queue.all_items() == [first, second]


def test_reorder_unlocks_when_the_run_ends(queue) -> None:
    tab, add = queue
    add("a")
    tab._on_mine_clicked()

    tab.worker_thread = None
    tab._recompute_buttons()

    assert tab.list_widget.dragDropMode() == QListWidget.DragDropMode.InternalMove


# ---------------------------------------------------------------------------
# The current-job strip
# ---------------------------------------------------------------------------


def test_a_run_publishes_a_task_when_a_registry_is_bound(queue, qtbot) -> None:
    tab, add = queue
    registry = TaskRegistry()
    tab.bind_task_registry(registry)
    add("a")

    tab._on_mine_clicked()

    try:
        assert registry.snapshot(tab.TASK_ID) is not None
        assert tab.current_job_strip.isVisibleTo(tab) is True
    finally:
        registry.shutdown()


def test_the_strip_names_the_item_being_mined(queue, qtbot) -> None:
    tab, add = queue
    registry = TaskRegistry()
    tab.bind_task_registry(registry)
    add("alpha")
    tab._on_mine_clicked()

    tab._on_item_started(0)

    try:
        assert "alpha" in tab.current_job_strip.line_label.full_text
    finally:
        registry.shutdown()


def test_a_run_without_a_registry_still_works(queue) -> None:
    """The registry is optional wiring; the queue must not depend on it."""
    tab, add = queue
    add("a")

    tab._on_mine_clicked()

    assert tab.worker_thread is not None
    assert tab.current_job_strip.isVisibleTo(tab) is False


def test_the_strip_collapses_when_the_run_ends(queue) -> None:
    tab, add = queue
    registry = TaskRegistry()
    tab.bind_task_registry(registry)
    add("a")
    tab._on_mine_clicked()

    try:
        tab._on_worker_finished()
        assert tab.current_job_strip.isVisibleTo(tab) is False
    finally:
        registry.shutdown()


# ---------------------------------------------------------------------------
# Keyboard bindings
# ---------------------------------------------------------------------------


def test_delete_is_bound_to_the_list_only(queue) -> None:
    tab, add = queue
    keys = {(s.key().toString(), s.context()) for s in tab.list_widget.findChildren(type(tab._delete_shortcut))}

    assert ("Del", Qt.ShortcutContext.WidgetShortcut) in keys


def test_alt_arrows_are_bound_to_the_list_only(queue) -> None:
    tab, add = queue
    keys = {(s.key().toString(), s.context()) for s in tab.list_widget.findChildren(type(tab._delete_shortcut))}

    assert ("Alt+Up", Qt.ShortcutContext.WidgetShortcut) in keys
    assert ("Alt+Down", Qt.ShortcutContext.WidgetShortcut) in keys


# ---------------------------------------------------------------------------
# YouTube-specific retry
# ---------------------------------------------------------------------------


def test_retry_reprobes_a_failed_youtube_probe(youtube_tab) -> None:
    """A probe failure is retried by probing again, not by mining a bad row."""
    item = youtube_tab._queue.add("https://youtu.be/x")
    item.status = YouTubeItemStatus.PROBE_ERROR
    item.error_message = "Video unavailable"
    youtube_tab._render_new_item(item)
    youtube_tab._list_items[item].setSelected(True)
    youtube_tab._add_flow.retry_probe = MagicMock()

    youtube_tab._on_retry_selected()

    youtube_tab._add_flow.retry_probe.assert_called_once_with(item)
    # It is not swept into the retry run: mining an unprobed row cannot succeed.
    assert youtube_tab.worker_thread is None
    assert item.status is YouTubeItemStatus.PROBE_ERROR

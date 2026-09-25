"""BatchProcessingTab queue-path progress wiring.

The queue path drives ONE composed bar:
- series-level counters   <- BatchQueueWorkerThread.queue_started / item_completed
                             / item_failed / item_pairs_progress
- per-episode sweep       <- progress_callback, composed as
                             (series done + episode pct) / total series

These pin the composed-bar slot behavior and the per-item status prefix.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def test_on_queue_started_primes_overall_bar(tab):
    tab._on_queue_started(4)
    assert tab._items_total == 4
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Starting queue processing..."


def test_item_started_sets_series_prefix(tab):
    tab._on_queue_started(4)
    tab._on_item_started("a", "Show A")
    assert tab.overall_progress_widget.status_label.text() == "Mining series 1 of 4: Show A"


def test_stage_detail_never_moves_the_series_bar(tab):
    """D18: the bar counts series; a half-done series is not one of them."""
    tab._on_queue_started(4)
    tab._on_item_started("a", "Show A")
    tab._on_progress_start(50, "Fetching definitions")
    tab._on_progress_update(25, "Fetching definitions")
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == (
        "Mining series 1 of 4: Show A — Fetching definitions (25 of 50)"
    )


def test_empty_stage_detail_keeps_the_item_prefix(tab):
    """An empty item description must not render a dangling 'name — '."""
    tab._on_queue_started(2)
    tab._on_item_started("a", "Show A")
    tab._on_progress_update(100, "")
    assert tab.overall_progress_widget.status_label.text() == "Mining series 1 of 2: Show A"


def test_progress_update_is_status_only_never_moves_the_bar(tab):
    """Per-item stage detail must not move the item-granular bar."""
    tab._items_total = 2
    tab._items_done = 1
    tab.overall_progress_widget.set_percent(50)
    tab._on_progress_update(10, "Extracting media")
    assert tab.overall_progress_widget.progress_bar.value() == 50
    assert "Extracting media" in tab.overall_progress_widget.status_label.text()


def test_queue_path_episode_ticks_fill_within_series(tab):
    """Real per-episode counts fill the bar between series boundaries.

    The composed value is (series done + episodes done / episodes total) over
    the series total -- every quantity a count the worker actually has, so the
    bar moves during a series without fabricating anything (the blank-bar bug).
    """
    pb = tab.overall_progress_widget.progress_bar
    tab._begin_run()
    tab._on_queue_started(2)
    tab._on_item_started("a", "Show A")
    tab._on_item_pairs_progress("a", 0, 12)
    assert pb.value() == 0
    tab._on_item_pairs_progress("a", 3, 12)
    assert pb.value() == 12
    tab._on_item_pairs_progress("a", 12, 12)
    assert pb.value() == 50
    tab._on_item_completed("a", 5)
    assert pb.value() == 50
    tab._on_item_started("b", "Show B")
    tab._on_item_pairs_progress("b", 0, 4)
    assert pb.value() == 50
    tab._on_item_pairs_progress("b", 2, 4)
    assert pb.value() == 75


def test_queue_path_episode_ticks_ignore_zero_totals(tab):
    """No series total yet, or an all-committed series with 0 pending pairs: no-op."""
    pb = tab.overall_progress_widget.progress_bar
    tab._begin_run()
    tab._on_item_pairs_progress("a", 1, 3)  # queue_started not seen yet
    assert pb.value() == 0
    tab._on_queue_started(2)
    tab._on_item_pairs_progress("a", 1, 0)  # empty pending set
    assert pb.value() == 0

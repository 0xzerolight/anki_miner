"""Cancel is one verb, states that it is waiting, and never erases what happened.

D22-A. Every run control in the app reads **Cancel**; pressing it turns the
control into a disabled **Cancelling…**, freezes the bar at its last true value,
and asks no questions. A confirmation prompt was rejected explicitly: it is the
one dialog guaranteed to appear at the moment the user has already decided.

The three failures this pins:

* a bar that keeps advancing after cancel, towards a finish that will not happen;
* a bar zeroed at the end of a cancel, erasing how far the run actually got;
* a run control that says Stop on one screen and Cancel on the next.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.models.batch_queue import QueueItemStatus


@pytest.fixture
def single(qapp, qtbot, test_config):
    tab = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)
    yield tab
    tab.deleteLater()


@pytest.fixture
def batch(qapp, qtbot, test_config):
    tab = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)
    yield tab
    tab.deleteLater()


# ---------------------------------------------------------------------------
# Single episode
# ---------------------------------------------------------------------------


def test_single_cancel_becomes_a_disabled_waiting_state(single):
    single.progress_widget.set_percent(40, "Extracting media")

    single._on_cancel_clicked()

    assert single.cancel_button.text() == "Cancelling…"
    assert not single.cancel_button.isEnabled()
    assert single.progress_widget.status_label.text() == "Cancelling…"


def test_single_cancel_asks_nothing(single, monkeypatch):
    """A prompt at the moment the user has already decided is pure friction."""
    from PyQt6.QtWidgets import QMessageBox

    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            MagicMock(side_effect=AssertionError(f"QMessageBox.{name} on Cancel")),
        )

    single._on_cancel_clicked()


def test_single_cancel_freezes_the_bar_against_late_progress(single):
    single.progress_widget.set_percent(40, "Extracting media")

    single._on_cancel_clicked()
    single._on_progress_stage(5, 5, "Creating Anki cards")
    single._on_progress_update(99, "late straggler")

    assert single.progress_widget.progress_bar.value() == 40


def test_single_cancelled_run_keeps_the_frozen_bar(single):
    single.progress_widget.set_percent(40, "Extracting media")
    single._on_cancel_clicked()

    single._restore_buttons()

    assert single.progress_widget.progress_bar.value() == 40
    assert single.progress_widget.status_label.text() == "Cancelled"


def test_single_run_control_reads_cancel(single, monkeypatch):
    monkeypatch.setattr(single, "_teardown_previous_run", lambda label: None)
    monkeypatch.setattr(single, "_start_processing", lambda *a, **k: None)

    assert single.cancel_button.text() == "Cancel"


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


def test_batch_cancel_becomes_a_disabled_waiting_state(batch):
    batch.overall_progress_widget.set_percent(25, "Episode 1/4")

    batch._on_cancel_clicked()

    assert batch.cancel_button.text() == "Cancelling…"
    assert not batch.cancel_button.isEnabled()


def test_batch_cancel_freezes_the_bar_against_late_progress(batch):
    batch._on_batch_started(4)
    batch._on_pair_finished(1, 4)
    assert batch.overall_progress_widget.progress_bar.value() == 25

    batch._on_cancel_clicked()
    batch._on_pair_finished(3, 4)

    assert batch.overall_progress_widget.progress_bar.value() == 25


def test_batch_cancelled_run_keeps_the_frozen_bar(batch):
    batch._on_batch_started(4)
    batch._on_pair_finished(1, 4)
    batch._on_cancel_clicked()

    batch._restore_buttons()

    assert batch.overall_progress_widget.progress_bar.value() == 25
    assert batch.overall_progress_widget.status_label.text() == "Cancelled"


def test_batch_run_control_reads_cancel(batch):
    batch._show_cancel_state()

    assert batch.cancel_button.text() == "Cancel"


# ---------------------------------------------------------------------------
# Retry arithmetic: the run's own denominator, not the queue's all-time one
# ---------------------------------------------------------------------------


def test_retrying_two_failures_after_eight_successes_counts_zero_of_two(batch):
    """Regression: the bar read "10/2" and sat pinned at 100% from item one."""
    batch.queue_panel.set_item_status = lambda item_id, status: None
    batch.queue_panel.set_processing_item_complete = lambda item_id, cards: None
    # Eight items the queue completed in earlier runs. The bar used to count
    # these because it read the queue's all-time totals rather than this run's.
    for i in range(8):
        item = batch.batch_queue.add_item(Path(f"/v{i}"), Path(f"/s{i}"), f"Show {i}")
        item.status = QueueItemStatus.COMPLETED
    assert batch.batch_queue.completed_count == 8

    batch._begin_run(queue_mode=True)
    batch._items_total = 2

    batch._on_item_completed("retry-a", 3)
    assert batch._items_done == 1
    assert batch.overall_progress_widget.progress_bar.value() == 50

    batch._on_item_completed("retry-b", 4)
    assert batch._items_done == 2
    assert batch.overall_progress_widget.progress_bar.value() == 100


def test_a_cancelled_batch_reports_its_cards_without_a_dialog(batch, monkeypatch):
    """The worker now hands over what it did on every exit; the tab must show
    that without popping "Batch Processing Complete" at someone who cancelled."""
    from PyQt6.QtWidgets import QMessageBox

    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            MagicMock(side_effect=AssertionError(f"QMessageBox.{name} after Cancel")),
        )
    batch._on_batch_started(4)
    batch._on_cancel_clicked()

    batch._on_processing_finished([MagicMock(cards_created=7, success=True)])

    log = batch.log_widget.text_edit.toPlainText()
    assert "1 of 4 episodes finished" in log
    assert "7 cards created" in log
    assert "Complete" not in batch.overall_progress_widget.status_label.text()


def test_the_same_item_reported_twice_is_counted_once(batch):
    batch.queue_panel.set_item_status = lambda item_id, status: None
    batch.queue_panel.set_processing_item_complete = lambda item_id, cards: None

    batch._begin_run(queue_mode=True)
    batch._items_total = 2
    batch._on_item_completed("a", 1)
    batch._on_item_completed("a", 1)

    assert batch._items_done == 1

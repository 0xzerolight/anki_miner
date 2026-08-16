"""BatchProcessingTab queue-worker startup wiring (G1 safety net).

The quick (manual-pair) path connects ``finished -> _restore_buttons`` so the
buttons recover once the worker thread ends. The queue path (``_start_queue_worker``)
did not, so a caught run-level failure (stale-dict gate, AnkiService construction)
left the action buttons stranded in the running state. This asserts the queue
path installs the same safety-net connection.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def test_start_queue_worker_connects_finished_to_restore_buttons(tab):
    """The queue path wires ``finished -> _restore_buttons`` (like the quick path)."""
    tab.worker_thread = None  # so _teardown_previous_run is a no-op
    fake_worker = MagicMock(name="BatchQueueWorkerThread")

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        return_value=fake_worker,
    ):
        tab._start_queue_worker()

    fake_worker.finished.connect.assert_any_call(tab._restore_buttons)
    fake_worker.start.assert_called_once()


def test_start_queue_worker_connects_item_pairs_progress(tab):
    """The queue path wires the within-series episode ticks into the Overall bar."""
    tab.worker_thread = None
    fake_worker = MagicMock(name="BatchQueueWorkerThread")

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread",
        return_value=fake_worker,
    ):
        tab._start_queue_worker()

    fake_worker.item_pairs_progress.connect.assert_any_call(tab._on_item_pairs_progress)


def test_an_all_complete_queue_is_told_how_to_run_again(tab):
    """The dead end names the way out instead of just refusing."""
    tab.queue_panel.has_only_completed_rows = lambda: True

    summary = tab._empty_run_summary()

    assert "Run selected" in summary
    assert "already complete" in summary


def test_an_unrunnable_queue_still_reports_the_plain_reason(tab):
    tab.queue_panel.has_only_completed_rows = lambda: False

    assert tab._empty_run_summary() == "No valid series in the queue to process."

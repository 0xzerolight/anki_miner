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

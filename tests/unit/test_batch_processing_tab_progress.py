"""BatchProcessingTab Quick-Processing progress wiring.

Quick Processing (folder pair) drives two bars like the queue path:
- Overall Progress  <- ManualPairWorkerThread.batch_started / pair_finished
- Current Episode   <- per-episode stage sweep via progress_callback

Before the fix the Overall bar was never touched during Quick Processing (it
stayed at "Ready"/0% the whole run). These pin both the slot behavior and the
signal wiring in _start_processing_with_pairs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, pyqtSignal

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


def test_on_batch_started_primes_overall_bar(tab):
    tab._on_batch_started(4)
    assert tab.overall_progress_widget.total == 4
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Starting batch processing..."


def test_on_pair_finished_advances_overall_bar(tab):
    tab._on_batch_started(4)
    tab._on_pair_finished(2, 4)
    # set_progress maps current/total to a percentage on a 0..100 bar.
    assert tab.overall_progress_widget.progress_bar.value() == 50
    assert tab.overall_progress_widget.status_label.text() == "Completed: 2/4"


class _FakeWorker(QObject):
    """Stand-in exposing exactly the signals _start_processing_with_pairs wires."""

    batch_started = pyqtSignal(int)
    pair_finished = pyqtSignal(int, int)
    result_ready = pyqtSignal(list)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.started = False

    def start(self):
        self.started = True


def test_start_processing_wires_overall_progress_signals(tab):
    """Emitting the worker's pair-level signals must move the Overall bar,
    proving _start_processing_with_pairs connected them."""
    with patch(
        "anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread",
        _FakeWorker,
    ):
        tab._start_processing_with_pairs([object(), object(), object()])

    worker = tab.worker_thread
    assert isinstance(worker, _FakeWorker)
    assert worker.started is True

    worker.batch_started.emit(3)
    assert tab.overall_progress_widget.total == 3

    worker.pair_finished.emit(3, 3)
    assert tab.overall_progress_widget.progress_bar.value() == 100
    assert tab.overall_progress_widget.status_label.text() == "Completed: 3/3"

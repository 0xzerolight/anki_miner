"""BatchProcessingTab Quick-Processing progress wiring.

Quick Processing (folder pair) drives ONE composed bar:
- pair-level counters   <- ManualPairWorkerThread.batch_started / pair_started
                           / pair_finished
- per-episode sweep     <- progress_callback, composed as
                           (pairs done + episode pct) / total pairs

These pin the composed-bar slot behavior and the signal wiring in
_start_processing_with_pairs.
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
    assert tab._items_total == 4
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Starting batch processing..."


def test_on_pair_finished_advances_composed_bar(tab):
    tab._on_batch_started(4)
    tab._on_pair_finished(2, 4)
    # Composed bar: (2 pairs done + 0) / 4 = 50%.
    assert tab.overall_progress_widget.progress_bar.value() == 50


def test_pair_started_sets_episode_prefix(tab):
    tab._on_batch_started(4)
    tab._on_pair_started(2, "ep02.mkv")
    assert tab.overall_progress_widget.status_label.text() == "Episode 2/4: ep02.mkv"


def test_quick_path_composes_episode_sweep(tab):
    """Per-episode stage percent composes into the whole-run bar with the
    persistent episode prefix glued onto the stage detail."""
    tab._on_batch_started(4)
    tab._on_pair_started(1, "ep01.mkv")
    tab._on_progress_update(50, "Fetching definitions")
    # (0 done + 50/100) / 4 = 12%
    assert tab.overall_progress_widget.progress_bar.value() == 12
    assert tab.overall_progress_widget.status_label.text() == "Episode 1/4: ep01.mkv — Fetching definitions"


def test_quick_path_empty_stage_detail_keeps_prefix(tab):
    """finish()'s on_progress(100, "") must not render a dangling 'name — '."""
    tab._on_batch_started(2)
    tab._on_pair_started(1, "ep01.mkv")
    tab._on_progress_update(100, "")
    assert tab.overall_progress_widget.status_label.text() == "Episode 1/2: ep01.mkv"


def test_queue_mode_progress_update_is_status_only(tab):
    """Queue path: per-episode sweep must not move the series-granular bar."""
    tab._queue_mode = True
    tab._items_total = 2
    tab._items_done = 1
    tab.overall_progress_widget.set_percent(50)
    tab._on_progress_update(10, "Extracting media")
    # Bar held at 50% (a composed write would have sawtoothed to 55%).
    assert tab.overall_progress_widget.progress_bar.value() == 50
    assert "Extracting media" in tab.overall_progress_widget.status_label.text()


class _FakeWorker(QObject):
    """Stand-in exposing exactly the signals _start_processing_with_pairs wires."""

    batch_started = pyqtSignal(int)
    pair_started = pyqtSignal(int, str)
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
    assert tab._items_total == 3

    worker.pair_started.emit(3, "ep03.mkv")
    assert tab.overall_progress_widget.status_label.text() == "Episode 3/3: ep03.mkv"

    worker.pair_finished.emit(3, 3)
    assert tab.overall_progress_widget.progress_bar.value() == 100

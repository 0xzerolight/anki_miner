"""Tests for BatchProcessingTab.release_dictionary_resources (Issue #30 follow-up).

BatchProcessingTab can host either ``ManualPairWorkerThread`` (attribute
``episode_processor``) or ``BatchQueueWorkerThread`` (attribute
``_current_processor``). Either worker keeps its processor alive after
finishing, so the release path must reach across the attr-name difference
to close sqlite handles before Settings → Remove / Re-import on Windows.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, test_config):
    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    yield widget
    widget.deleteLater()


def _idle_worker(*, attr: str):
    """Build a MagicMock worker that's idle and exposes the given processor attr."""
    worker = MagicMock(name=f"Worker[{attr}]")
    worker.isRunning.return_value = False
    # Wipe spec-derived attrs so getattr only finds the one we want.
    worker.episode_processor = None
    worker._current_processor = None
    setattr(worker, attr, MagicMock(name="Processor"))
    return worker


def test_release_when_no_worker_returns_true(tab):
    tab.worker_thread = None
    assert tab.release_dictionary_resources() is True


def test_release_with_running_worker_returns_false(tab):
    worker = MagicMock(name="Worker")
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    worker.isRunning.assert_called()


def test_release_handles_manual_pair_worker(tab):
    """ManualPairWorkerThread uses ``episode_processor``."""
    worker = _idle_worker(attr="episode_processor")
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    worker.episode_processor.definition_service.close.assert_called_once_with()


def test_release_handles_queue_worker(tab):
    """BatchQueueWorkerThread uses ``_current_processor``."""
    worker = _idle_worker(attr="_current_processor")
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    worker._current_processor.definition_service.close.assert_called_once_with()


def test_release_prefers_episode_processor_when_both_set(tab):
    """Defensive: if both attrs exist (shouldn't happen), prefer episode_processor."""
    worker = MagicMock(name="Worker")
    worker.isRunning.return_value = False
    worker.episode_processor = MagicMock(name="EpisodeProc")
    worker._current_processor = MagicMock(name="CurrentProc")
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    worker.episode_processor.definition_service.close.assert_called_once_with()
    worker._current_processor.definition_service.close.assert_not_called()


def test_release_idempotent(tab):
    worker = _idle_worker(attr="episode_processor")
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert worker.episode_processor.definition_service.close.call_count == 2

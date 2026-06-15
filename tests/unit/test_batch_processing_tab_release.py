"""Tests for BatchProcessingTab.release_dictionary_resources (Issue #30 follow-up).

BatchProcessingTab can host either ``ManualPairWorkerThread`` or
``BatchQueueWorkerThread``. Both expose their retained processor through
the typed ``curation_processor`` property (T-60), so the release path no
longer reaches across worker-specific attribute names; it closes the
sqlite handles through the ``EpisodeProcessor.release_dictionary_resources``
facade before Settings → Remove / Re-import on Windows.
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


def _idle_worker(processor):
    """Build a MagicMock worker exposing ``processor`` via ``curation_processor``."""
    worker = MagicMock(name="Worker")
    worker.isRunning.return_value = False
    worker.curation_processor = processor
    return worker


def test_release_when_no_worker_returns_true(tab):
    tab.worker_thread = None
    assert tab.release_dictionary_resources() is True


def test_release_with_running_worker_returns_false(tab, facade_processor):
    worker = _idle_worker(facade_processor)
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    worker.isRunning.assert_called()
    facade_processor.definition_service.close.assert_not_called()


def test_release_with_idle_worker_closes_definition_service_via_facade(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    facade_processor.definition_service.close.assert_called_once_with()


def test_release_with_idle_worker_no_processor_returns_true(tab):
    # Worker never created a processor (e.g. failed before the first item):
    # nothing to close, but removal may proceed.
    tab.worker_thread = _idle_worker(None)
    assert tab.release_dictionary_resources() is True


def test_release_idempotent(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert facade_processor.definition_service.close.call_count == 2


# ---------------------------------------------------------------------------
# Sequential-rerun teardown (Windows back-to-back-mining freeze)
# ---------------------------------------------------------------------------


def test_teardown_joins_and_closes_prior_processor(tab):
    """_teardown_previous_run cancels, joins, then closes the old processor."""
    old_processor = MagicMock(name="OldProcessor")
    old_worker = MagicMock(name="OldWorker")
    old_worker.wait.return_value = True
    old_worker.curation_processor = old_processor
    tab.worker_thread = old_worker

    tab._teardown_previous_run("batch")

    old_worker.finished.disconnect.assert_called_once_with(tab._restore_buttons)
    old_worker.cancel.assert_called_once_with()
    old_worker.wait.assert_called_once()
    old_processor.close.assert_called_once_with()


def test_teardown_no_worker_is_noop(tab):
    tab.worker_thread = None
    tab._teardown_previous_run("batch")  # must not raise


def test_teardown_tolerates_no_processor(tab):
    old_worker = MagicMock(name="OldWorker")
    old_worker.wait.return_value = True
    old_worker.curation_processor = None
    tab.worker_thread = old_worker
    tab._teardown_previous_run("batch")  # must not raise
    old_worker.cancel.assert_called_once_with()


def test_teardown_skips_processor_close_on_join_timeout(tab):
    """On wait() timeout the worker is still live; closing its sqlite handles
    from the GUI thread would race the worker — so the close is SKIPPED. The
    new run still proceeds (caller reassigns ``self.worker_thread``)."""
    old_processor = MagicMock(name="OldProcessor")
    old_worker = MagicMock(name="OldWorker")
    old_worker.wait.return_value = False  # join times out → worker still running
    old_worker.curation_processor = old_processor
    tab.worker_thread = old_worker

    tab._teardown_previous_run("batch")

    old_worker.cancel.assert_called_once_with()
    old_worker.wait.assert_called_once()
    # MUST NOT close the old processor under a still-running worker.
    old_processor.close.assert_not_called()

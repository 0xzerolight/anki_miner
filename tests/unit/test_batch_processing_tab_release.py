"""BatchProcessingTab's sequential-rerun teardown (Windows back-to-back-mining freeze).

``_teardown_previous_run`` cancels, joins and closes the prior run's worker
and processor, and leaks rather than closes them when the join times out. The
``release_dictionary_resources`` contract this screen shares with Single and
Deck Builder lives in ``test_tab_release_contract.py``.
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

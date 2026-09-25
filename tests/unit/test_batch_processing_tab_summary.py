"""BatchProcessingTab queue-run completion summary (Issue #51, D20).

``process_episode`` returns a ProcessingResult with errors populated rather than
raising, so the summary must distinguish a full success from a partial one
instead of presenting every finish as a success (Issue #51).

The summary itself is no longer a modal box. It is the screen's inline run
receipt, sealed when the worker thread ends — the old dialog interrupted after
every run and fired even when the user had just cancelled (D20).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.models.processing import ProcessingResult, TerminalOutcome


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


@pytest.fixture
def clock(monkeypatch):
    """Freeze the receipt's clock so the summary's duration is exact."""
    state = {"t": 500.0}
    monkeypatch.setattr(MiningTabBase, "_receipt_now", staticmethod(lambda: (state["t"], state["t"])))
    return state


@pytest.fixture
def task_registry(qapp):
    registry = TaskRegistry()
    yield registry
    registry.shutdown()


def _finish(tab, results: list[ProcessingResult]) -> str:
    for i in range(len(results)):
        tab.batch_queue.add_item(Path(f"/v{i}"), Path(f"/s{i}"), f"Show {i}")
    with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread", MagicMock()):
        tab._start_queue_worker()
    for item, result in zip(tab.batch_queue.get_all_items(), results, strict=True):
        if result.success:
            tab._on_item_completed(item.id, result.cards_created)
        else:
            tab._on_item_failed(item.id, result.errors[0], result.cards_created)
    tab._on_queue_finished(sum(r.cards_created for r in results))
    tab._on_run_thread_finished()
    return tab._receipt_widget.summary_text


def test_failed_results_are_named_consistently_across_run_surfaces(tab, clock, task_registry):
    """Mixed results: the receipt states how many of the series completed."""
    tab.bind_task_registry(task_registry)
    failed = ProcessingResult(
        total_words_found=0,
        new_words_found=0,
        cards_created=0,
        errors=["Error: deck missing"],
    )
    succeeded = ProcessingResult(total_words_found=10, new_words_found=5, cards_created=2)

    summary = _finish(tab, [failed, succeeded])

    assert summary == "Finished with errors — 1 of 2 series completed; 2 notes added in 00m 00s"
    assert tab.overall_progress_widget.status_label.text() == "Finished with errors — see log"
    assert tab._receipt_widget.receipt.outcome is TerminalOutcome.PARTIAL
    assert task_registry.snapshot(tab.TASK_ID).outcome is TaskOutcome.FAILED


def test_all_success_reads_as_a_complete_run(tab, clock):
    r1 = ProcessingResult(total_words_found=8, new_words_found=4, cards_created=2)
    r2 = ProcessingResult(total_words_found=12, new_words_found=6, cards_created=3)

    summary = _finish(tab, [r1, r2])

    assert summary == "Mining complete — 2 series, 5 notes added in 00m 00s"


def test_no_dialog_is_opened_on_the_queue_path(tab, clock):
    """The one path ends in a receipt, and the module has no modal left to open.

    This used to patch ``batch_processing_tab.QMessageBox`` and assert it was
    never called; the attribute is gone, which is the same claim.
    """
    from anki_miner.gui.widgets import batch_processing_tab as module

    failed = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=["boom"])
    succeeded = ProcessingResult(total_words_found=8, new_words_found=4, cards_created=2)

    _finish(tab, [failed, succeeded])

    assert not hasattr(module, "QMessageBox")

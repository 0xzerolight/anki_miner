"""BatchProcessingTab manual-pair completion summary (Issue #51, D20).

``process_episode`` returns a ProcessingResult with errors populated rather than
raising, so the summary must distinguish a full success from a partial one
instead of presenting every finish as a success (Issue #51).

The summary itself is no longer a modal box. It is the screen's inline run
receipt, sealed when the worker thread ends — the old dialog interrupted after
every run and fired even when the user had just cancelled (D20).
"""

from __future__ import annotations

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


def _finish(tab, results: list[ProcessingResult], *, pairs: int) -> str:
    with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
        tab._start_processing_with_pairs([object()] * pairs)
    tab._on_processing_finished(results=results)
    tab._on_run_thread_finished()
    return tab._receipt_widget.summary_text


def test_failed_results_are_named_consistently_across_run_surfaces(tab, clock, task_registry):
    """Mixed results: the receipt states how many of the episodes completed."""
    tab.bind_task_registry(task_registry)
    failed = ProcessingResult(
        total_words_found=0,
        new_words_found=0,
        cards_created=0,
        errors=["Error: deck missing"],
    )
    succeeded = ProcessingResult(total_words_found=10, new_words_found=5, cards_created=2)

    summary = _finish(tab, [failed, succeeded], pairs=2)

    assert summary == "Finished with errors — 1 of 2 episodes completed; 2 notes added in 00m 00s"
    assert tab.overall_progress_widget.status_label.text() == "Finished with errors — see log"
    assert tab._receipt_widget.receipt.outcome is TerminalOutcome.PARTIAL
    assert task_registry.snapshot(tab.TASK_ID).outcome is TaskOutcome.FAILED


def test_all_success_reads_as_a_complete_run(tab, clock):
    r1 = ProcessingResult(total_words_found=8, new_words_found=4, cards_created=2)
    r2 = ProcessingResult(total_words_found=12, new_words_found=6, cards_created=3)

    summary = _finish(tab, [r1, r2], pairs=2)

    assert summary == "Mining complete — 2 episodes, 5 notes added in 00m 00s"


def test_no_dialog_is_opened_on_either_path(tab, clock):
    failed = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=["boom"])
    succeeded = ProcessingResult(total_words_found=8, new_words_found=4, cards_created=2)

    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as message_box:
        _finish(tab, [failed, succeeded], pairs=2)
        _finish(tab, [succeeded], pairs=1)

    message_box.warning.assert_not_called()
    message_box.information.assert_not_called()

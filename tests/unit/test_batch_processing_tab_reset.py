"""Tests for BatchProcessingTab terminal bar states.

Convention: success pins the bar at 100% with a summary; cancel resets to
"Cancelled"; error resets to "Failed — see log"; every run START resets.
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


def _prime_progress(tab):
    """Push the bar off zero so we can verify the terminal state applies."""
    tab._items_total = 3
    tab.overall_progress_widget.set_percent(60, "Series 2/3")


def test_queue_finished_success_pins_summary(tab):
    """Success: bar pinned at 100% with a card-count summary."""
    _prime_progress(tab)
    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox.information"):
        tab._on_queue_finished(total_cards=5)
    assert tab.overall_progress_widget.progress_bar.value() == 100
    assert tab.overall_progress_widget.status_label.text() == "Complete — 5 cards created"


def test_queue_finished_cancelled_resets_to_cancelled(tab):
    """Cancelled queue run: reset + "Cancelled", never a success summary."""
    _prime_progress(tab)
    tab._cancel_requested = True
    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox.information"):
        tab._on_queue_finished(total_cards=0)
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Cancelled"


def test_queue_finished_failed_resets_to_failed(tab):
    """Run-level fatal (error + queue_finished): "Failed — see log"."""
    _prime_progress(tab)
    tab._on_queue_worker_error("stale dicts")
    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox.information"):
        tab._on_queue_finished(total_cards=0)
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Failed — see log"


def test_processing_finished_pins_summary(tab):
    """Manual-pair completion pins the summary (result_ready implies no cancel)."""
    _prime_progress(tab)
    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox.information"):
        tab._on_processing_finished(results=[])
    assert tab.overall_progress_widget.progress_bar.value() == 100
    assert tab.overall_progress_widget.status_label.text() == "Complete — 0 cards created"


def test_processing_error_resets_to_failed(tab):
    _prime_progress(tab)
    tab._on_processing_error("boom")
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Failed — see log"


def test_run_start_resets_previous_end_state(tab):
    """A new run must clear the previous run's pinned summary."""
    tab.overall_progress_widget.show_completion("Complete — 5 cards created")
    tab._begin_run(queue_mode=False)
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Ready"
    assert tab._cancel_requested is False
    assert tab._run_failed is False


def test_restore_buttons_recovers_cancelled_quick_run(tab):
    """Quick-path cancel: result_ready is suppressed, so QThread.finished →
    _restore_buttons must replace "Cancelling..." with "Cancelled"."""
    _prime_progress(tab)
    tab._cancel_requested = True
    tab._restore_buttons()
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Cancelled"

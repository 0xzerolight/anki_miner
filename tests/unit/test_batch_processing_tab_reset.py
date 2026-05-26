"""Tests for BatchProcessingTab progress reset (Issue: bars stayed stuck on cancel).

The error path always reset both progress widgets, but cancel and normal
completion did not — so cancelling halfway left both bars frozen at their
last partial value. Reset must fire on every terminal path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def _prime_progress(tab):
    """Push both bars off zero so we can verify reset() runs."""
    tab.overall_progress_widget.set_determinate(3)
    tab.overall_progress_widget.set_progress(2, 3, "Series 2/3")
    tab.current_progress_widget.set_determinate(10)
    tab.current_progress_widget.set_progress(7, 10, "Item 7/10")


def test_queue_finished_resets_both_bars(tab):
    """Regression for bug 2: cancel emits queue_finished → bars must reset."""
    _prime_progress(tab)
    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox.information"):
        tab._on_queue_finished(total_cards=5)
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.current_progress_widget.progress_bar.value() == 0
    assert tab.overall_progress_widget.status_label.text() == "Ready"
    assert tab.current_progress_widget.status_label.text() == "Ready"


def test_processing_finished_resets_both_bars(tab):
    """Regression for bug 2: manual-pair completion must reset bars too."""
    _prime_progress(tab)
    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox.information"):
        tab._on_processing_finished(results=[])
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.current_progress_widget.progress_bar.value() == 0


def test_processing_error_still_resets_both_bars(tab):
    """Existing behavior preserved: error path already reset, must continue to."""
    _prime_progress(tab)
    tab._on_processing_error("boom")
    assert tab.overall_progress_widget.progress_bar.value() == 0
    assert tab.current_progress_widget.progress_bar.value() == 0

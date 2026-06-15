"""Tests for BatchProcessingTab manual-pair completion summary dialog (Issue #51).

When episodes fail validation, process_episode returns a ProcessingResult with
errors populated (success == False) rather than raising. The completion dialog
must distinguish between full-success and partial/total-failure runs instead of
presenting every finish as a success.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.models.processing import ProcessingResult


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


def test_failed_results_show_warning_with_failure_count(tab):
    """Mixed results: warning dialog naming the failure count (Issue #51).

    One failed episode (errors populated) and one successful episode should
    trigger QMessageBox.warning (not information), and the message must include
    the failure count and the correct total-cards figure.
    """
    failed = ProcessingResult(
        total_words_found=0,
        new_words_found=0,
        cards_created=0,
        errors=["Error: deck missing"],
    )
    succeeded = ProcessingResult(
        total_words_found=10,
        new_words_found=5,
        cards_created=2,
    )

    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as mock_msgbox:
        tab._on_processing_finished(results=[failed, succeeded])

    mock_msgbox.warning.assert_called_once()
    mock_msgbox.information.assert_not_called()

    _parent, _title, message = mock_msgbox.warning.call_args.args
    assert "1 episode(s) failed" in message
    assert "Total cards created: 2" in message


def test_all_success_shows_information_dialog(tab):
    """Regression: all-success run must keep showing the information dialog."""
    r1 = ProcessingResult(total_words_found=8, new_words_found=4, cards_created=2)
    r2 = ProcessingResult(total_words_found=12, new_words_found=6, cards_created=3)

    with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as mock_msgbox:
        tab._on_processing_finished(results=[r1, r2])

    mock_msgbox.information.assert_called_once()
    mock_msgbox.warning.assert_not_called()

    _parent, _title, message = mock_msgbox.information.call_args.args
    assert "Total cards created: 5" in message

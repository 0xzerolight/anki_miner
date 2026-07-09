"""Tests for BatchProcessingTab retry-run cancel affordance (T-22).

``_retry_failed_items`` disabled the action buttons but never called
``_show_cancel_state()`` like the other two run paths (``_process_queue`` and
``_start_processing_with_pairs``), so the Cancel button stayed hidden for the
whole retry run — the run was uncancellable and any open curation dialog could
not be released. The retry path must surface Cancel like every other run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.models.batch_queue import QueueItemStatus


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


def _seed_failed_item(tab):
    """Add a retryable (ERROR, under max_retries) item to the batch queue."""
    item = tab.batch_queue.add_item(Path("/anime"), Path("/subs"), "Failed Show")
    item.status = QueueItemStatus.ERROR
    item.error_message = "boom"
    return item


def test_retry_failed_items_shows_cancel_button(tab):
    """Regression for T-22: retry run must reveal the Cancel button."""
    _seed_failed_item(tab)

    # The retry run kicks off a real worker thread; stub it out so the test
    # only exercises the button-state wiring.
    with patch.object(tab, "_start_queue_worker"):
        tab._retry_failed_items()

    # The tab is never shown in tests, so isVisible() is always False;
    # isHidden() tracks the explicit show()/hide() flag we care about.
    assert not tab.cancel_button.isHidden()
    assert tab.cancel_button.isEnabled()
    # The two normal-run buttons must be hidden during the retry run.
    assert tab.process_pairs_button.isHidden()

"""FIX G7: failed queue items render an error badge, not a Pending fallback.

``QueueItemWidget._update_status_badge`` had no ``"error"`` entry, so a failed
item fell back to the Pending badge; and ``BatchProcessingTab._on_item_failed``
never called ``set_item_status``, so a failed row showed Processing during the
run then Pending after. Both are fixed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget


def test_error_status_renders_error_badge(qapp, qtbot):
    """set_status('error') shows the Error badge with the 'error' style key."""
    widget = QueueItemWidget("Series")
    qtbot.addWidget(widget)

    widget.set_status("error")

    assert widget.status_badge.text() == "Error"
    assert widget.status_badge.property("status") == "error"
    # Must NOT fall back to the Pending badge.
    assert widget.status_badge.text() != "Pending"


def test_on_item_failed_sets_error_status(qapp, qtbot, test_config):
    """_on_item_failed marks the failed row with the 'error' status."""
    tab = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)

    calls: list = []
    tab.queue_panel.set_item_status = lambda item_id, status: calls.append((item_id, status))
    tab._advance_queue_bar = lambda: None

    tab._on_item_failed("item-42", "boom")

    assert ("item-42", "error") in calls

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


def test_error_status_renders_the_failed_word(qapp, qtbot):
    """set_status('error') reads Failed, the chips' word, never a Ready fallback."""
    widget = QueueItemWidget("Series")
    qtbot.addWidget(widget)

    widget.set_status("error")

    assert widget.state_label.text() == "Failed"


def test_complete_zero_card_row_never_says_ready(qapp, qtbot):
    widget = QueueItemWidget("Series")
    qtbot.addWidget(widget)

    widget.set_episode_count(2)
    widget.set_status("complete")
    widget.set_cards_created(0)

    assert widget.state_label.text() == "Complete"
    assert widget.aside_label.text() == "2 episodes"
    assert widget.result_label.text() == "Cards: 0"


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
    tab._advance_queue_bar = lambda item_id: None

    tab._on_item_failed("item-42", "boom")

    assert ("item-42", "error") in calls

"""D20 item 1: every mining screen's run button says Mine."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")


def test_single_says_mine_episode(single_tab):
    assert single_tab.process_button.text() == "Mine Episode"


def test_batch_says_mine_queue(batch_tab):
    button = batch_tab.queue_panel.process_queue_button
    assert button.text() == "Mine Queue"
    assert button.toolTip() == "Mine every series in the queue"

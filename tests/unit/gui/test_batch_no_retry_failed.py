"""D6 item 1: Batch retries through the selection bar like every other queue."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QPushButton


def test_there_is_no_retry_failed_button(batch_tab):
    assert not hasattr(batch_tab, "retry_button")
    assert not hasattr(batch_tab, "_retry_failed_items")
    texts = [button.text() for button in batch_tab.findChildren(QPushButton)]
    assert "Retry Failed" not in texts


def test_retry_selected_is_still_there(batch_tab):
    assert batch_tab.queue_panel.queue_controls.retry_button.text() == "Retry selected"

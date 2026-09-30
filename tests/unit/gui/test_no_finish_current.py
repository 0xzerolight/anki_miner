"""D3: a running queue offers Pause and Cancel, and no third stop."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.queue_controls_bar import QueueControlsBar
from tests.unit.gui._screens import add_audiobook


def test_the_bar_has_no_finish_control(qtbot):
    bar = QueueControlsBar()
    qtbot.addWidget(bar)
    bar.set_running(True)

    assert not hasattr(bar, "finish_button")
    assert not hasattr(QueueControlsBar, "finish_current_requested")
    assert not bar.pause_button.isHidden()


def test_list_queues_lost_the_handler(audiobook_tab, tmp_path):
    add_audiobook(audiobook_tab, tmp_path, "a")
    audiobook_tab._on_mine_clicked()

    assert not hasattr(audiobook_tab, "_on_finish_current_requested")


def test_batch_lost_the_handler(batch_tab):
    assert not hasattr(batch_tab, "_on_finish_current_requested")

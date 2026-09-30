"""A10: Pause is offered only while another item follows the one being mined."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.queue_controls_bar import QueueControlsBar
from tests.unit.gui._screens import add_audiobook


def test_the_bar_hides_pause_when_told_nothing_follows(qtbot):
    bar = QueueControlsBar()
    qtbot.addWidget(bar)
    bar.set_running(True)

    bar.set_pause_available(False)

    assert bar.pause_button.isHidden()


def test_a_paused_run_keeps_resume_whatever_follows(qtbot):
    bar = QueueControlsBar()
    qtbot.addWidget(bar)
    bar.set_running(True)
    bar.set_paused(True, done=1, total=2)

    bar.set_pause_available(False)

    assert not bar.pause_button.isHidden()
    assert bar.pause_button.text() == "Resume"


def test_a_repeat_set_running_keeps_the_paused_state(qtbot):
    bar = QueueControlsBar()
    qtbot.addWidget(bar)
    bar.set_running(True)
    bar.set_paused(True, done=1, total=3)

    bar.set_running(True)

    assert bar.pause_button.text() == "Resume"


def test_list_queue_last_item_hides_pause(audiobook_tab, tmp_path):
    tab = audiobook_tab
    add_audiobook(tab, tmp_path, "a")
    add_audiobook(tab, tmp_path, "b")
    tab._on_mine_clicked()

    tab._on_item_started(0)
    assert not tab.queue_controls.pause_button.isHidden()

    tab._on_item_started(1)
    assert tab.queue_controls.pause_button.isHidden()


def test_batch_last_series_hides_pause(batch_tab):
    batch_tab.queue_panel.set_locked(True)
    batch_tab._items_total = 2
    batch_tab._items_done = 0
    batch_tab._on_item_started("id-1", "Show A")
    assert not batch_tab.queue_panel.queue_controls.pause_button.isHidden()

    batch_tab._items_done = 1
    batch_tab._on_item_started("id-2", "Show B")
    assert batch_tab.queue_panel.queue_controls.pause_button.isHidden()

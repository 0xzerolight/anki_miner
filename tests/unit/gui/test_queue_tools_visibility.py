"""A01 + D6 item 3: queue tools appear at the moment they can act."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QPushButton

from anki_miner.gui.widgets.queue_controls_bar import QUEUE_TOOLS_MIN_ROWS, QueueControlsBar
from tests.unit.gui._screens import add_audiobook


def _bar(qtbot) -> QueueControlsBar:
    bar = QueueControlsBar()
    qtbot.addWidget(bar)
    return bar


def _counts(bar: QueueControlsBar, total: int) -> None:
    bar.set_counts(total=total, ready=total, failed=0, complete=0)


def test_the_threshold_is_six_rows():
    assert QUEUE_TOOLS_MIN_ROWS == 6


def test_an_empty_queue_shows_no_tools(qtbot):
    bar = _bar(qtbot)
    clear = QPushButton("Clear")
    bar.set_clear_button(clear)

    assert all(button.isHidden() for button in bar.filter_buttons.values())
    assert bar.search_edit.isHidden()
    assert bar.counter_label.isHidden()
    assert clear.isHidden()
    assert bar.run_button.isHidden()
    assert bar.retry_button.isHidden()
    assert bar.remove_button.isHidden()


def test_the_first_row_brings_the_counter_and_clear(qtbot):
    bar = _bar(qtbot)
    clear = QPushButton("Clear")
    bar.set_clear_button(clear)

    _counts(bar, 1)

    assert not bar.counter_label.isHidden()
    assert not clear.isHidden()
    assert bar.search_edit.isHidden()


def test_six_rows_bring_the_chips_and_search(qtbot):
    bar = _bar(qtbot)
    _counts(bar, 6)

    assert not any(button.isHidden() for button in bar.filter_buttons.values())
    assert not bar.search_edit.isHidden()


def test_dropping_below_six_rows_resets_the_view(qtbot):
    bar = _bar(qtbot)
    _counts(bar, 6)
    bar.filter_buttons["failed"].click()
    bar.search_edit.setText("ep")
    seen_filters: list[str] = []
    seen_search: list[str] = []
    bar.filter_changed.connect(seen_filters.append)
    bar.search_changed.connect(seen_search.append)

    _counts(bar, 5)

    assert bar.active_filter() == "all"
    assert bar.search_text() == ""
    assert seen_filters == ["all"]
    assert seen_search == [""]


def test_selection_actions_appear_only_with_a_selection(qtbot):
    bar = _bar(qtbot)
    _counts(bar, 2)

    bar.set_selection_count(1)
    assert not bar.run_button.isHidden()
    assert not bar.remove_button.isHidden()

    bar.set_selection_count(0)
    assert bar.run_button.isHidden()


def test_list_queue_tools_follow_the_rows(audiobook_tab, tmp_path):
    tab = audiobook_tab
    controls = tab.queue_controls
    assert controls.counter_label.isHidden()
    assert tab.clear_button.isHidden()

    add_audiobook(tab, tmp_path, "a")
    assert not controls.counter_label.isHidden()
    assert not tab.clear_button.isHidden()
    assert controls.search_edit.isHidden()

    for index in range(5):
        add_audiobook(tab, tmp_path, f"b{index}")
    assert not controls.search_edit.isHidden()

    tab.list_widget.item(0).setSelected(True)
    assert not controls.run_button.isHidden()


def test_a_queue_restored_from_the_last_session_shows_its_tools(audiobook_tab, tmp_path):
    """Review focus: a queue restored at launch gets the same tools as one built by hand.

    Restore renders rows through its own path (restore_queue_snapshot), not the
    Add button, so the counter, Clear, the chips (from six rows) and the search
    must follow the restored rows too. One pair was moved away since the last
    session: it comes back as a failed row, and the Failed chip can find it.
    """
    from anki_miner.gui.utils import queue_state_store
    from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot

    rows = []
    for index in range(7):
        audio = tmp_path / f"book{index}.m4b"
        subtitle = tmp_path / f"book{index}.srt"
        if index:  # book0's files are gone since the last session
            audio.touch()
            subtitle.touch()
        rows.append(
            QueueItemSnapshot(item_id=f"restored-{index}", source=queue_state_store.file_pair_source(audio, subtitle))
        )
    tab = audiobook_tab

    assert tab.restore_queue_snapshot(QueueSnapshot(key=tab.QUEUE_STATE_KEY, items=tuple(rows))) == 7

    controls = tab.queue_controls
    assert not controls.counter_label.isHidden()
    assert not tab.clear_button.isHidden()
    assert not controls.search_edit.isHidden()
    assert all(not button.isHidden() for button in controls.filter_buttons.values())
    assert controls.run_button.isHidden()  # nothing selected yet
    controls.filter_buttons["failed"].click()
    visible = [index for index in range(tab.list_widget.count()) if not tab.list_widget.item(index).isHidden()]
    assert len(visible) == 1


def test_list_queue_clear_lives_in_the_bar(audiobook_tab):
    assert audiobook_tab.clear_button.parentWidget() is audiobook_tab.queue_controls


def test_batch_clear_is_renamed_and_lives_in_the_bar(batch_tab):
    panel = batch_tab.queue_panel
    assert panel.clear_button.text() == "Clear"
    assert panel.clear_button.parentWidget() is panel.queue_controls

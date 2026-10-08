"""D2 + A02: Batch rows are one calm line with the chips' state words and a real episode count."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.base.queue_row import QueueRowWidget
from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.gui.widgets.panels.queue_panel import QueuePanel
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget
from anki_miner.models.batch_queue import BatchQueue


def _season(tmp_path, name: str, episodes: int):
    video = tmp_path / f"{name}-video"
    subs = tmp_path / f"{name}-subs"
    video.mkdir()
    subs.mkdir()
    for number in range(1, episodes + 1):
        (video / f"Show - {number:02d}.mkv").touch()
        (subs / f"Show - {number:02d}.srt").touch()
    return video, subs


@pytest.fixture
def panel(qtbot):
    widget = QueuePanel(queue=BatchQueue())
    qtbot.addWidget(widget)
    return widget


class TestTheRow:
    def test_it_is_a_one_line_queue_row(self, qtbot):
        row = QueueItemWidget("Show")
        qtbot.addWidget(row)
        assert isinstance(row, QueueRowWidget)
        assert row.sizeHint().height() == metric_row_height(row, vertical_padding=QueueRowWidget.ROW_PADDING_Y)

    @pytest.mark.parametrize(
        ("status", "word"),
        [("pending", "Ready"), ("processing", "Running"), ("error", "Failed"), ("complete", "Complete")],
    )
    def test_it_uses_the_chip_words(self, qtbot, status, word):
        row = QueueItemWidget("Show")
        qtbot.addWidget(row)
        row.set_status(status)
        assert row.state_label.text() == word

    def test_it_counts_episodes_in_the_aside(self, qtbot):
        row = QueueItemWidget("Show")
        qtbot.addWidget(row)
        assert row.aside_label.text() == ""
        row.set_episode_count(1)
        assert row.aside_label.text() == "1 episode(s)"
        row.set_episode_count(3)
        assert row.aside_label.text() == "3 episode(s)"

    def test_a_finished_row_shows_its_cards(self, qtbot):
        row = QueueItemWidget("Show")
        qtbot.addWidget(row)
        row.set_cards_created(11)
        assert row.result_label.text() == ""
        row.set_status("complete")
        assert row.result_label.text() == "Cards: 11"

    def test_the_folders_are_in_the_tooltip(self, qtbot, tmp_path):
        row = QueueItemWidget("Show")
        qtbot.addWidget(row)
        row.set_folders(tmp_path / "v", tmp_path / "s", tmp_path / "t")
        tip = row.toolTip()
        assert str(tmp_path / "v") in tip
        assert str(tmp_path / "s") in tip
        assert str(tmp_path / "t") in tip
        assert "Double-click to edit" in tip

    def test_it_has_no_row_buttons(self, qtbot):
        row = QueueItemWidget("Show")
        qtbot.addWidget(row)
        assert not hasattr(row, "edit_button")
        assert not hasattr(row, "remove_button")


class TestThePanel:
    def test_added_series_count_their_episodes(self, panel, qtbot, tmp_path):
        video, subs = _season(tmp_path, "a", 3)
        panel.add_series(display_name="A", video_folder=video, subtitle_folder=subs, subtitle_offset=0.0)
        row = panel.queue_item_widgets[0]
        qtbot.waitUntil(lambda: row.get_episode_count() == 3, timeout=5000)

    def test_restored_series_count_their_episodes(self, panel, qtbot, tmp_path):
        video, subs = _season(tmp_path, "a", 2)
        panel.restore_item(
            item_id="id-1",
            display_name="A",
            video_folder=video,
            subtitle_folder=subs,
            subtitle_offset=0.0,
            status="pending",
            cards_created=0,
            retry_count=0,
            error_message="",
        )
        row = panel.queue_item_widgets[0]
        qtbot.waitUntil(lambda: row.get_episode_count() == 2, timeout=5000)

    def test_the_counter_folds_in_series_and_episodes(self, panel, qtbot, tmp_path):
        for name, episodes in (("a", 3), ("b", 2)):
            video, subs = _season(tmp_path, name, episodes)
            panel.add_series(display_name=name, video_folder=video, subtitle_folder=subs, subtitle_offset=0.0)
        qtbot.waitUntil(lambda: sum(w.get_episode_count() for w in panel.queue_item_widgets) == 5, timeout=5000)
        assert panel.queue_controls.counter_label.text() == "2 series · 5 episode(s) · 2 ready"
        assert not hasattr(panel, "queue_stats_label")

    def test_an_empty_queue_says_so(self, panel):
        assert not panel.empty_label.isHidden()
        assert panel.empty_label.text() == "Queue is empty"

    def test_double_click_opens_edit(self, panel, tmp_path, monkeypatch):
        video, subs = _season(tmp_path, "a", 1)
        panel.add_series(display_name="A", video_folder=video, subtitle_folder=subs, subtitle_offset=0.0)
        edited: list = []
        monkeypatch.setattr(panel, "_edit_item", edited.append)

        panel.list_widget.itemDoubleClicked.emit(panel.list_widget.item(0))

        assert edited == [panel.queue_item_widgets[0]]

    def test_edit_joins_the_selection_bar_for_exactly_one_row(self, panel, tmp_path, monkeypatch):
        for name in ("a", "b"):
            video, subs = _season(tmp_path, name, 1)
            panel.add_series(display_name=name, video_folder=video, subtitle_folder=subs, subtitle_offset=0.0)
        edited: list = []
        monkeypatch.setattr(panel, "_edit_item", edited.append)
        edit = panel.queue_controls.edit_button
        assert edit is not None
        assert edit.isHidden()

        panel.list_widget.item(0).setSelected(True)
        assert not edit.isHidden()
        edit.click()
        assert edited == [panel.queue_item_widgets[0]]

        panel.list_widget.item(1).setSelected(True)
        assert edit.isHidden()

"""A18: Subtitle Files shows one line when empty and file names when not."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel

from anki_miner.gui.utils.qt_helpers import COPY_ROLE
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"


@pytest.fixture
def tab(qtbot, test_config):
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingSubtitlesTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget


def _files(tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n", encoding="utf-8")
        paths.append(path)
    return paths


def test_an_empty_list_is_one_line(tab):
    assert not tab.empty_label.isHidden()
    assert tab.empty_label.text() == "Add subtitle files, or drop them here."
    assert tab.file_list.isHidden()
    assert tab.remove_selected_button.isHidden()
    assert tab.clear_button.isHidden()


def test_no_card_heading_repeats_the_tab(tab):
    assert "Subtitle Files" not in [label.text() for label in tab.findChildren(QLabel)]


def test_rows_show_names_and_keep_full_paths(tab, tmp_path):
    first, second = _files(tmp_path, "ep01.srt", "ep02.srt")

    tab._add_paths([first, second])

    assert [tab.file_list.item(i).text() for i in range(2)] == ["ep01.srt", "ep02.srt"]
    assert tab.file_list.item(0).toolTip() == str(first)
    assert tab.file_list.item(0).data(COPY_ROLE) == str(first)
    assert tab.listed_paths() == [first, second]
    assert tab.empty_label.isHidden()
    assert not tab.file_list.isHidden()


def test_duplicates_are_still_skipped(tab, tmp_path):
    (first,) = _files(tmp_path, "ep01.srt")
    tab._add_paths([first])
    tab._add_paths([first])
    assert tab.listed_paths() == [first]


def test_mine_is_offered_and_explains_an_empty_list(tab):
    assert tab.mine_button.isEnabled()
    tab._on_mine_clicked()
    assert tab.issue_banner().current_issue().summary == "Add at least one subtitle file first."


def test_a_vanished_file_is_named_under_details(tab, tmp_path):
    (first,) = _files(tmp_path, "ep01.srt")
    tab._add_paths([first])
    first.unlink()

    tab._on_mine_clicked()

    issue = tab.issue_banner().current_issue()
    assert issue.summary == "A listed file no longer exists."
    assert issue.details == str(first)

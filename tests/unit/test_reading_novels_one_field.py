"""D7-B: Novels takes a book or a folder of books in one field."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel

from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_URLS = "anki_miner.gui.widgets.reading_novels_tab.urls_from_event"


@pytest.fixture
def tab(qtbot, test_config):
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingNovelsTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        widget._queue_worker_cls = worker_cls  # type: ignore[attr-defined]
        yield widget


def _url(path: Path):
    url = MagicMock()
    url.toLocalFile.return_value = str(path)
    return url


def test_one_field_takes_a_book_or_a_folder(tab):
    assert not hasattr(tab, "folder_selector")
    assert not hasattr(tab, "folder_mine_button")
    assert tab.book_selector.folder_button is not None


def test_no_card_heading_repeats_the_tab(tab):
    texts = [label.text() for label in tab.findChildren(QLabel)]
    assert "Novel" not in texts
    assert "Book Folder" not in texts


def test_mine_on_a_folder_mines_every_book(tab, tmp_path):
    (tmp_path / "a.txt").write_text("一", encoding="utf-8")
    (tmp_path / "b.txt").write_text("二", encoding="utf-8")
    tab.book_selector.set_path(str(tmp_path))

    tab._on_mine_clicked()

    assert tab.worker_thread is not None
    assert len(tab._run_items) == 2


def test_mine_on_a_book_mines_it(tab, tmp_path):
    book = tmp_path / "a.txt"
    book.write_text("一", encoding="utf-8")
    tab.book_selector.set_path(str(book))

    tab._on_mine_clicked()

    assert [item.title for item in tab._run_items] == ["a"]


def test_an_empty_field_is_refused_in_the_banner(tab):
    tab._on_mine_clicked()
    assert tab.issue_banner().current_issue().summary == "Choose a book or a folder of books first."


def test_a_wrong_kind_of_file_is_refused_in_the_banner(tab, tmp_path):
    volume = tmp_path / "vol.cbz"
    volume.touch()
    tab.book_selector.set_path(str(volume))

    tab._on_mine_clicked()

    assert tab.issue_banner().current_issue().summary == "Choose an .epub or .txt book, or a folder of books."


def test_a_dropped_subtitle_names_the_right_tab(tab, tmp_path):
    subtitle = tmp_path / "ep.srt"
    subtitle.touch()
    event = MagicMock()
    with patch(_URLS, return_value=[_url(subtitle)]):
        tab.dropEvent(event)
    assert "Subtitle files are mined in Reading → Subtitle Files." in tab.log_widget.text_edit.toPlainText()

"""D7-B: Manga takes a volume or a folder in one field, and Mine classifies it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel

from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_URLS = "anki_miner.gui.widgets.reading_manga_tab.urls_from_event"


@pytest.fixture
def tab(qtbot, test_config):
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingMangaTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget


def _url(path: Path):
    url = MagicMock()
    url.toLocalFile.return_value = str(path)
    return url


def test_one_field_takes_a_file_or_a_folder(tab):
    assert not hasattr(tab, "volume_folder_selector")
    assert not hasattr(tab, "folder_mine_button")
    assert tab.volume_file_selector.folder_button is not None


def test_no_card_heading_repeats_the_tab(tab):
    texts = [label.text() for label in tab.findChildren(QLabel)]
    assert "Volume" not in texts
    assert "Manga Folder" not in texts


def test_mine_classifies_a_folder(tab, tmp_path, monkeypatch):
    seen: list[Path] = []
    monkeypatch.setattr(tab, "_detect_and_launch", seen.append)
    tab.volume_file_selector.set_path(str(tmp_path))

    tab._on_mine_clicked()

    assert seen == [tmp_path]


def test_mine_classifies_a_volume(tab, tmp_path, monkeypatch):
    volume = tmp_path / "vol1.cbz"
    volume.touch()
    seen: list[Path] = []
    monkeypatch.setattr(tab, "_detect_and_launch", seen.append)
    tab.volume_file_selector.set_path(str(volume))

    tab._on_mine_clicked()

    assert seen == [volume]


def test_an_empty_field_is_refused_in_the_banner(tab):
    tab._on_mine_clicked()
    assert tab.issue_banner().current_issue().summary == "Choose a manga volume or folder first."


def test_a_wrong_kind_of_file_is_refused_in_the_banner(tab, tmp_path):
    book = tmp_path / "book.txt"
    book.touch()
    tab.volume_file_selector.set_path(str(book))

    tab._on_mine_clicked()

    assert tab.issue_banner().current_issue().summary == "Choose a .mokuro, .cbz or .zip volume, or a manga folder."


def test_mine_is_enabled_when_idle(tab):
    assert tab.mine_button.isEnabled()


def test_a_dropped_folder_fills_the_one_field(tab, tmp_path):
    event = MagicMock()
    with patch(_URLS, return_value=[_url(tmp_path)]):
        tab.dropEvent(event)
    assert tab.volume_file_selector.get_path() == str(tmp_path)


def test_a_dropped_subtitle_names_the_right_tab(tab, tmp_path):
    subtitle = tmp_path / "ep.srt"
    subtitle.touch()
    event = MagicMock()
    with patch(_URLS, return_value=[_url(subtitle)]):
        tab.dropEvent(event)
    assert "Subtitle files are mined in Reading → Subtitle Files." in tab.log_widget.text_edit.toPlainText()

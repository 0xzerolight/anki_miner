"""Regression tests: long paths render from the start, not scrolled off-screen."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.enhanced.file_selector import FileSelector

_app = QApplication.instance() or QApplication([])

LONG_PATH = "/home/light/Downloads/Code Geass - Roze of the Recapture S01 1080p Dual Audio WEBRip DD+ x265-EMBER/[EMBER] Code Geass - Dakkan no Roze - 01.mkv"


@pytest.fixture
def widget():
    w = FileSelector(label="Video File")
    yield w
    w.deleteLater()


def test_set_path_resets_cursor_to_start(widget: FileSelector):
    widget.set_path(LONG_PATH)
    assert widget.input.cursorPosition() == 0
    assert widget.input.text() == LONG_PATH


def test_set_path_sets_tooltip(widget: FileSelector):
    widget.set_path(LONG_PATH)
    assert widget.input.toolTip() == LONG_PATH


def test_status_label_does_not_wrap(widget: FileSelector):
    assert widget.status_label.wordWrap() is False

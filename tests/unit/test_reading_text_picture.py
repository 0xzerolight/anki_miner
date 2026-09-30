"""A19: the card picture is an optional extra, and the caption lives in the placeholder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PIL import Image
from PyQt6.QtWidgets import QLabel

from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"


@pytest.fixture
def tab(qtbot, test_config):
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingTextTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        widget._queue_worker_cls = worker_cls  # type: ignore[attr-defined]
        yield widget


def test_the_caption_moved_into_the_placeholder(tab):
    assert tab.text_edit.placeholderText() == (
        "Paste the text you want to mine. Cards from pasted text have no sentence audio from a recording."
    )
    texts = [label.text() for label in tab.findChildren(QLabel)]
    assert "Pasted Text" not in texts
    assert "Paste text and mine it into Anki cards — no audio is extracted." not in texts


def test_the_picture_starts_as_one_quiet_button(tab):
    assert not hasattr(tab, "image_selector")
    assert tab.picture_button.text() == "Add card picture…"
    assert not tab.picture_button.isHidden()
    assert tab.picture_row.isHidden()


def test_a_chosen_picture_becomes_a_name_and_a_cross(tab, tmp_path):
    picture = tmp_path / "cover.png"
    Image.new("RGB", (4, 4)).save(picture)

    tab.set_card_picture(picture)

    assert tab.picture_button.isHidden()
    assert not tab.picture_row.isHidden()
    assert tab.picture_name_label.text() == "cover.png"

    tab.picture_clear_button.click()
    assert tab._picture_path is None
    assert not tab.picture_button.isHidden()


def test_the_picked_file_reaches_the_run(tab, tmp_path):
    picture = tmp_path / "cover.png"
    Image.new("RGB", (4, 4)).save(picture)
    tab.set_card_picture(picture)
    tab.text_edit.setPlainText("今日は雨です。")

    tab._on_mine_clicked()

    assert tab._run_items[0].source.image_root == picture


def test_mine_is_offered_and_explains_empty_text(tab):
    assert tab.mine_button.isEnabled()
    tab._on_mine_clicked()
    assert tab.issue_banner().current_issue().summary == "Paste some text first."


def test_an_unreadable_picture_is_refused_in_the_banner(tab, tmp_path):
    bogus = tmp_path / "broken.png"
    bogus.write_bytes(b"not an image")
    tab.set_card_picture(bogus)
    tab.text_edit.setPlainText("今日は雨です。")

    tab._on_mine_clicked()

    assert tab.issue_banner().current_issue().summary == (
        "That picture cannot be read. Pick another, or remove it to mine without one."
    )

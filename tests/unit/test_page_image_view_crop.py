"""F-11: the curator's manga page view can outline what the card picture keeps."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QImage, QPixmap

from anki_miner.gui.widgets.page_image_view import PageImageView
from anki_miner.services.reading.images import manga_crop_box

BOX = (100, 200, 300, 600)


def _pixmap(width: int = 800, height: int = 1000) -> QPixmap:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(255, 255, 255))
    return QPixmap.fromImage(image)


def _view(qtbot) -> PageImageView:
    view = PageImageView()
    qtbot.addWidget(view)
    return view


def test_the_crop_preview_is_off_by_default(qtbot):
    view = _view(qtbot)
    view.show_page(_pixmap(), BOX, "p.1")

    assert view.current_crop is None


def test_turned_on_it_names_the_card_crop(qtbot):
    view = _view(qtbot)
    view.set_crop_preview(True)
    view.show_page(_pixmap(), BOX, "p.1")

    assert view.current_crop == manga_crop_box(BOX, (800, 1000)) == (50, 100, 350, 700)


def test_a_page_without_a_box_has_no_crop(qtbot):
    view = _view(qtbot)
    view.set_crop_preview(True)
    view.show_page(_pixmap(), None, "p.1")

    assert view.current_crop is None


def test_painting_with_the_preview_on_draws_a_page(qtbot):
    view = _view(qtbot)
    view.resize(240, 300)
    view.set_crop_preview(True)
    view.show_page(_pixmap(), BOX, "p.1")

    grabbed = view.grab()

    assert not grabbed.isNull()

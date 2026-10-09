"""Paint one item-view cell to an image, for delegate tests that have to see pixels."""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QRect, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QAbstractItemDelegate, QAbstractItemView, QStyleOptionViewItem


def paint_cell(
    delegate: QAbstractItemDelegate,
    view: QAbstractItemView,
    index: QModelIndex,
    size: tuple[int, int] = (400, 32),
) -> QImage:
    """Paint ``index`` through ``delegate`` onto a white image, unselected.

    The option is built exactly as the view builds one for its own paint
    (``initViewItemOption``: font, palette, elide mode, ``widget``), then given
    this image's rect; the delegate's ``initStyleOption`` fills in the item.
    Nothing is selected, so no highlight muddies the ink.
    """
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    option = QStyleOptionViewItem()
    view.initViewItemOption(option)
    option.rect = QRect(0, 0, size[0], size[1])
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return image


def ink_span(image: QImage) -> tuple[int, int]:
    """The first and last column holding any pixel that is not the background."""
    background = image.pixel(0, 0)
    columns = [x for x in range(image.width()) if any(image.pixel(x, y) != background for y in range(image.height()))]
    assert columns, "nothing was painted"
    return columns[0], columns[-1]

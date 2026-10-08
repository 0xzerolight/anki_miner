"""Item 2: a power-user disclosure in Settings is one more checkbox row, not a heading.

The global QGroupBox rule drew "Edit the pattern (advanced)" and "Customize
marker field names" at heading size and weight inside a frame, so a collapsed
one was an empty box whose title outranked the page's own section names, and
its tick box was Qt's unthemed default.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QColor, QFont, QImage
from PyQt6.QtWidgets import QCheckBox, QGroupBox, QStyle, QStyleOptionButton, QStyleOptionGroupBox, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.gui.widgets.panels.sentences_settings_panel import SentencesSettingsPanel


@pytest.fixture(params=["light", "dark"])
def themed(qapp, request):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(Theme.get_stylesheet(request.param))
    yield
    qapp.setStyleSheet(previous)


def _sentences():
    panel = SentencesSettingsPanel()
    return panel, panel.subtitle_regex_group, panel._subtitle_regex_body, panel.use_subtitle_regex_checkbox


def _anki():
    panel = AnkiSettingsPanel()
    return panel, panel.card_type_names_group, panel._card_type_names_body, panel.strict_card_order_checkbox


@pytest.fixture(params=[_sentences, _anki], ids=["sentences", "anki"])
def disclosure(qtbot, themed, request):
    """(panel, disclosure, its body, a plain checkbox row on the same page), shown."""
    panel, group, body, checkbox = request.param()
    qtbot.addWidget(panel)
    panel.resize(900, panel.sizeHint().height())
    panel.show()
    qtbot.waitExposed(panel)
    return panel, group, body, checkbox


def _group_rect(group: QGroupBox, control: QStyle.SubControl) -> QRect:
    option = QStyleOptionGroupBox()
    group.initStyleOption(option)
    return group.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, option, control, group)


def _checkbox_rect(checkbox: QCheckBox, element: QStyle.SubElement) -> QRect:
    option = QStyleOptionButton()
    checkbox.initStyleOption(option)
    return checkbox.style().subElementRect(element, option, checkbox)


def _crop(panel: QWidget, widget: QWidget, rect: QRect) -> QImage:
    """``rect`` (in ``widget`` coordinates) as rendered on the whole panel."""
    return panel.grab().toImage().copy(rect.translated(widget.mapTo(panel, rect.topLeft()) - rect.topLeft()))


def test_the_title_is_body_text(disclosure):
    _, group, _, _ = disclosure

    assert group.font().pixelSize() == FONT_SIZES.body
    assert group.font().weight() == QFont.Weight.Normal


def test_collapsed_it_is_one_checkbox_row(disclosure, qtbot):
    _, group, _, checkbox = disclosure
    group.setChecked(True)
    group.setChecked(False)
    qtbot.wait(50)

    assert group.height() == checkbox.height()


def test_no_frame_is_drawn_beside_the_title(disclosure):
    panel, group, _, _ = disclosure
    label = _group_rect(group, QStyle.SubControl.SC_GroupBoxLabel)
    strip = group.rect().adjusted(label.right() + 8, 0, 0, 0)

    image = _crop(panel, group, strip)
    colours = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}

    assert len(colours) == 1


@pytest.mark.parametrize(
    ("checked", "focused"),
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["unticked", "ticked", "unticked-focused", "ticked-focused"],
)
def test_the_tick_box_matches_a_checkbox(disclosure, qtbot, checked, focused):
    panel, group, _, checkbox = disclosure
    group.setChecked(checked)
    checkbox.setChecked(checked)
    theirs = _checkbox_rect(checkbox, QStyle.SubElement.SE_CheckBoxIndicator)
    ours = _group_rect(group, QStyle.SubControl.SC_GroupBoxCheckBox)
    # The group's rect also carries the margin that spaces its title; compare boxes.
    ours.setWidth(theirs.width())

    def tick_box(widget: QWidget, rect: QRect) -> QImage:
        # Focus recolours a tick box's border, and showing the panel focuses
        # its first control, so each box is grabbed with the focus set as asked.
        if focused:
            widget.setFocus()
            qtbot.waitUntil(widget.hasFocus)
        else:
            widget.clearFocus()
        qtbot.wait(20)
        return _crop(panel, widget, rect)

    assert tick_box(group, ours) == tick_box(checkbox, theirs)


def test_the_tick_box_is_not_clipped(disclosure):
    _, group, _, _ = disclosure
    box = _group_rect(group, QStyle.SubControl.SC_GroupBoxCheckBox)

    assert box.top() >= 0
    assert box.bottom() < group.height()


def _ink_centre(image: QImage, panel: QWidget, widget: QWidget, width: int) -> float:
    """Ink-weighted x of the text right of ``widget``'s tick box, in its own coordinates."""
    origin = widget.mapTo(panel, QPoint(0, 0))
    start = origin.x() + 23  # past the 22 px tick box
    background = QColor(image.pixel(start, origin.y())).lightness()
    total = weighted = 0
    for x in range(start, start + width):
        for y in range(origin.y(), origin.y() + 22):
            ink = abs(QColor(image.pixel(x, y)).lightness() - background)
            total += ink
            weighted += ink * x
    assert total, "no text drawn"
    return weighted / total - origin.x()


def test_the_title_text_lines_up_with_checkbox_text(disclosure, qtbot):
    # Measured on screen, not from style rects: Qt draws a group box title away
    # from the label rect its style reports. Same text in both, so the ink
    # centres match when the text starts at the same x; the title is centred in
    # its rect, so it may land a fraction of a pixel off.
    panel, group, _, checkbox = disclosure
    checkbox.setText(group.title())
    qtbot.wait(20)
    image = panel.grab().toImage()
    width = group.fontMetrics().horizontalAdvance(group.title()) + 8

    assert abs(_ink_centre(image, panel, group, width) - _ink_centre(image, panel, checkbox, width)) < 1


def test_open_its_body_sits_under_the_title_text(disclosure, qtbot):
    _, group, body, checkbox = disclosure
    group.setChecked(True)
    qtbot.waitUntil(body.isVisible)
    text = _checkbox_rect(checkbox, QStyle.SubElement.SE_CheckBoxContents)
    box = _group_rect(group, QStyle.SubControl.SC_GroupBoxCheckBox)

    assert body.x() == text.left()
    assert body.y() > box.bottom()

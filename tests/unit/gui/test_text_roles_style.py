"""E02 and E04: helper text is readable, output values read as values, the rail fits.

Helper text was 11 px italic with an 8 px left inset, so it neither lined up with
the control above it nor read comfortably; the output folder value used the same
muted small style as a hint, so a value looked like help copy.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles import FONT_SIZES
from anki_miner.gui.resources.styles.theme import Theme

THEME = "light"


@pytest.fixture(autouse=True)
def _themed(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(Theme.get_stylesheet(THEME))
    yield
    qapp.setStyleSheet(previous)


def _label(qtbot, name: str) -> QLabel:
    label = QLabel("Next to source")
    label.setObjectName(name)
    qtbot.addWidget(label)
    label.show()
    label.ensurePolished()
    return label


def test_helper_text_is_upright_caption_size(qtbot):
    label = _label(qtbot, "helper-text")

    assert label.font().italic() is False
    assert label.font().pixelSize() == FONT_SIZES.caption


def test_helper_text_has_no_left_inset(qtbot):
    label = _label(qtbot, "helper-text")

    assert label.contentsRect().left() == 0


def test_an_output_value_is_body_text_in_the_normal_colour(qtbot):
    label = _label(qtbot, "output-location-value")

    assert label.font().pixelSize() == FONT_SIZES.body
    assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(Theme.get_colors(THEME)["text"])


def test_the_settings_rail_uses_the_smallest_vertical_step():
    raw = (get_resource_dir() / "styles" / "common.qss").read_text(encoding="utf-8")
    # Sliced by hand: a rule body holds "${...}" braces, so a [^}]* regex stops early.
    start = raw.index("QListWidget#settings-nav::item {")
    body = raw[start : raw.index("\n}", start)]

    assert "padding: ${spacing-xxs}px ${spacing-sm}px ${spacing-xxs}px ${spacing-md}px;" in body

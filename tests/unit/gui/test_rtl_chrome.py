"""Right-to-left interface (ar/fa/he): chrome that Qt does not mirror by itself."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLineEdit

from anki_miner.gui.resources.styles.theme import Theme, mirror_border_sides
from anki_miner.gui.widgets.base.form_panel import FormPanel


@pytest.fixture
def rtl_app(qapp):
    qapp.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    yield qapp
    qapp.setLayoutDirection(Qt.LayoutDirection.LeftToRight)


def test_border_sides_and_corners_swap():
    qss = "a { border-left: 2px solid; border-right-color: red; border-top-left-radius: 3px; padding-left: 4px; }"
    assert mirror_border_sides(qss) == (
        "a { border-right: 2px solid; border-left-color: red; border-top-right-radius: 3px; padding-left: 4px; }"
    )


def test_an_rtl_app_gets_the_mirrored_stylesheet(rtl_app):
    """The Settings navigator's selection marker sits on the outer (right) edge."""
    rtl_sheet = Theme.get_stylesheet()
    rtl_app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    ltr_sheet = Theme.get_stylesheet()

    assert rtl_sheet == mirror_border_sides(ltr_sheet)
    assert rtl_sheet != ltr_sheet


def test_a_latin_field_label_sits_beside_its_field_in_an_rtl_form(qtbot, rtl_app):
    panel = FormPanel("Panel")
    qtbot.addWidget(panel)
    panel.add_field("Anki", QLineEdit())
    label = panel._form_rows[0][0]

    assert label.alignment() & Qt.AlignmentFlag.AlignAbsolute
    assert label.alignment() & Qt.AlignmentFlag.AlignLeft  # the field is on the label's left


def test_a_field_label_keeps_right_alignment_in_an_ltr_form(qtbot):
    panel = FormPanel("Panel")
    qtbot.addWidget(panel)
    panel.add_field("Anki", QLineEdit())

    assert panel._form_rows[0][0].alignment() & Qt.AlignmentFlag.AlignRight

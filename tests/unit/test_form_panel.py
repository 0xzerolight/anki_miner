"""Tests for FormPanel helper text rendering."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base.form_panel import FormPanel

_app = QApplication.instance() or QApplication([])


def _find_helper_text_label(panel: FormPanel) -> QLabel | None:
    """Locate any QLabel with objectName 'helper-text'."""
    for label in panel.findChildren(QLabel):
        if label.objectName() == "helper-text":
            return label
    return None


def test_helper_sets_tooltip_on_widget():
    """Helper text must be set as the widget's tooltip, not rendered inline."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox("Bold target word")
    helper_text = "Matches the Yomitan {cloze-prefix}<b>{cloze-body}</b>{cloze-suffix} idiom."
    panel.add_field("", widget, helper=helper_text)

    assert widget.toolTip() == helper_text


def test_helper_does_not_create_inline_label():
    """No child 'helper-text' QLabel should be created for field helper text."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox("Bold target word")
    helper_text = "Matches the Yomitan {cloze-prefix}<b>{cloze-body}</b>{cloze-suffix} idiom."
    panel.add_field("", widget, helper=helper_text)

    helper_label = _find_helper_text_label(panel)
    assert helper_label is None, "No 'helper-text' QLabel should exist for field helper"


def test_helper_plain_prose_sets_tooltip():
    """Plain-prose helpers are also set as tooltip."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox()
    panel.add_field("Label", widget, helper="Plain helper text with no markup")

    assert widget.toolTip() == "Plain helper text with no markup"


def test_no_helper_leaves_tooltip_empty():
    """Fields with no helper leave widget tooltip empty/unchanged."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox()
    panel.add_field("Label", widget)

    assert widget.toolTip() == ""


def test_field_without_helper_still_renders():
    """A field with no helper must still be added to the form."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox()
    result = panel.add_field("Label", widget)

    assert result is widget


def test_field_with_helper_returns_widget():
    """add_field must return the input widget even when helper is provided."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox()
    result = panel.add_field("Label", widget, helper="Some help")

    assert result is widget


def test_field_with_helper_no_label_does_not_create_container():
    """When label is empty and helper is set, no container QWidget should wrap the input."""
    panel = FormPanel("Test Panel")
    widget = QCheckBox()
    panel.add_field("", widget, helper="Some help")

    # The widget itself should be a direct child of the form; no extra container
    # wrapping it. We verify by checking widget.toolTip() is set correctly (done
    # above) and that no extra plain QWidget children exist beyond the panel itself.
    containers = [c for c in panel.findChildren(QWidget) if type(c) is QWidget and c is not panel]
    assert len(containers) == 0, f"Unexpected plain QWidget containers found: {containers}"


# ---------------------------------------------------------------------------
# Density / spacing tests (Task B)
# ---------------------------------------------------------------------------


def _find_section_label(panel: FormPanel, text: str) -> QLabel | None:
    """Return the first QLabel child whose text matches *text*, or None."""
    for label in panel.findChildren(QLabel):
        if label.text() == text:
            return label
    return None


def test_form_layout_spacing_is_xs():
    """_new_form_layout must use SPACING.xs (8) between form rows."""
    panel = FormPanel("Spacing Test")
    # _form_layout is the initial form layout created during _setup_ui
    assert panel._form_layout.spacing() == SPACING.xs


def test_main_layout_spacing_is_xs():
    """_setup_ui must set main layout spacing to SPACING.xs (8)."""
    panel = FormPanel("Spacing Test")
    assert panel.main_layout.spacing() == SPACING.xs


def test_section_heading_font_pixelsize_is_body_sm():
    """add_section heading QLabel must use FONT_SIZES.body_sm (13) pixel size."""
    panel = FormPanel("Section Test")
    panel.add_section("My Section")

    heading = _find_section_label(panel, "My Section")
    assert heading is not None, "Section heading QLabel not found"
    assert heading.font().pixelSize() == FONT_SIZES.body_sm


def test_section_heading_font_weight_is_demibold():
    """add_section heading QLabel must keep DemiBold weight."""
    panel = FormPanel("Section Test")
    panel.add_section("My Section")

    heading = _find_section_label(panel, "My Section")
    assert heading is not None, "Section heading QLabel not found"
    assert heading.font().weight() == QFont.Weight.DemiBold

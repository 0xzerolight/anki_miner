"""Regression tests for FormPanel section/field ordering.

Bug: add_section() appended labels to the main QVBoxLayout, but every
add_field() call wrote to a single form layout registered once at init.
Result was every section heading rendering at the bottom of the panel,
under all the fields, instead of above its own fields.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QFormLayout, QLabel  # noqa: E402

from anki_miner.gui.widgets.base.form_panel import FormPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _layout_items(layout):
    return [layout.itemAt(i) for i in range(layout.count())]


def _layout_sequence(layout):
    """Flat sequence of ('label', text) and ('form', form_layout) entries.

    Skips the panel header row, which is the first child layout containing
    the title QLabel. Only sections + form layouts matter for the bug.
    """
    sequence = []
    for i, item in enumerate(_layout_items(layout)):
        widget = item.widget()
        sub = item.layout()
        if widget is not None and isinstance(widget, QLabel) and i > 0:
            sequence.append(("label", widget.text()))
        elif isinstance(sub, QFormLayout):
            sequence.append(("form", sub))
    return sequence


def test_section_heading_renders_above_its_fields(qapp):
    panel = FormPanel("Test")
    panel.add_section("Alpha")
    panel.add_field("a-field", QCheckBox())
    panel.add_section("Beta")
    panel.add_field("b-field", QCheckBox())

    seq = _layout_sequence(panel.main_layout)
    kinds = [kind for kind, _ in seq]
    labels = [val for kind, val in seq if kind == "label"]

    # Order must be: initial form (empty), Alpha label, Alpha form, Beta label, Beta form.
    assert kinds == ["form", "label", "form", "label", "form"], kinds
    assert labels == ["Alpha", "Beta"]


def test_each_section_owns_its_own_form_layout(qapp):
    panel = FormPanel("Test")
    panel.add_section("Alpha")
    panel.add_field("a-field", QCheckBox())
    panel.add_section("Beta")
    panel.add_field("b-field-1", QCheckBox())
    panel.add_field("b-field-2", QCheckBox())

    forms = [item for kind, item in _layout_sequence(panel.main_layout) if kind == "form"]
    initial, alpha, beta = forms
    assert initial.rowCount() == 0
    assert alpha.rowCount() == 1
    assert beta.rowCount() == 2


def test_fields_before_any_section_stay_in_initial_form(qapp):
    panel = FormPanel("Test")
    panel.add_field("pre", QCheckBox())
    panel.add_section("Alpha")
    panel.add_field("post", QCheckBox())

    forms = [item for kind, item in _layout_sequence(panel.main_layout) if kind == "form"]
    assert len(forms) == 2
    assert forms[0].rowCount() == 1  # pre-section field
    assert forms[1].rowCount() == 1  # post-section field

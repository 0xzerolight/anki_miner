"""Tests for FormPanel helper text rendering."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QCheckBox, QLabel, QLineEdit, QWidget

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.base.form_panel import FormPanel


def _find_helper_text_label(panel: FormPanel) -> QLabel | None:
    """Locate any QLabel with objectName 'helper-text'."""
    for label in panel.findChildren(QLabel):
        if label.objectName() == "helper-text":
            return label
    return None


def test_helper_sets_tooltip_on_widget(qtbot):
    """Helper text must be set as the widget's tooltip, not rendered inline."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QCheckBox("Bold target word")
    helper_text = "Matches the Yomitan {cloze-prefix}<b>{cloze-body}</b>{cloze-suffix} idiom."
    panel.add_field("", widget, helper=helper_text)

    assert widget.toolTip() == helper_text


def test_helper_does_not_create_inline_label(qtbot):
    """No child 'helper-text' QLabel should be created for field helper text."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QCheckBox("Bold target word")
    helper_text = "Matches the Yomitan {cloze-prefix}<b>{cloze-body}</b>{cloze-suffix} idiom."
    panel.add_field("", widget, helper=helper_text)

    helper_label = _find_helper_text_label(panel)
    assert helper_label is None, "No 'helper-text' QLabel should exist for field helper"


def test_helper_plain_prose_sets_tooltip(qtbot):
    """Plain-prose helpers are also set as tooltip."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QCheckBox()
    panel.add_field("Label", widget, helper="Plain helper text with no markup")

    assert widget.toolTip() == "Plain helper text with no markup"


def test_no_helper_leaves_tooltip_empty(qtbot):
    """Fields with no helper leave widget tooltip empty/unchanged."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QCheckBox()
    panel.add_field("Label", widget)

    assert widget.toolTip() == ""


def test_field_without_helper_still_renders(qtbot):
    """A field with no helper must still be added to the form."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QCheckBox()
    result = panel.add_field("Label", widget)

    assert result is widget


def test_field_with_helper_returns_widget(qtbot):
    """add_field must return the input widget even when helper is provided."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QCheckBox()
    result = panel.add_field("Label", widget, helper="Some help")

    assert result is widget


def test_form_rows_have_buddy_relation(qtbot):
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
    widget = QLineEdit()

    panel.add_field("Label", widget)

    label = next(label for label in panel.findChildren(QLabel) if label.text() == "Label:")
    assert label.buddy() is widget


def test_field_with_helper_no_label_does_not_create_container(qtbot):
    """When label is empty and helper is set, no container QWidget should wrap the input."""
    panel = FormPanel("Test Panel")
    qtbot.addWidget(panel)
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


def test_form_layout_spacing_is_xxs(qtbot):
    """_new_form_layout must use SPACING.xxs (4) between form rows.

    Tightened by D40: rows inside one card are more closely related to each
    other than the cards are, so they take the smaller of the two gaps.
    """
    panel = FormPanel("Spacing Test")
    qtbot.addWidget(panel)
    # _form_layout is the initial form layout created during _setup_ui
    assert panel._form_layout.spacing() == SPACING.xxs


def test_main_layout_spacing_is_xs(qtbot):
    """_setup_ui must set main layout spacing to SPACING.xs (8)."""
    panel = FormPanel("Spacing Test")
    qtbot.addWidget(panel)
    assert panel.main_layout.spacing() == SPACING.xs


# ---------------------------------------------------------------------------
# Type ladder: page title 20 / subheading 16 / body 14, under the app QSS
# ---------------------------------------------------------------------------


@pytest.fixture(params=["light", "dark"])
def themed(request, qapp):
    """Apply a real theme stylesheet: its QWidget font rule beats setFont, so only QSS counts."""
    previous = qapp.styleSheet()
    qapp.setStyleSheet(Theme.get_stylesheet(request.param))
    yield
    qapp.setStyleSheet(previous)


def _rendered(label: QLabel) -> tuple[int, int]:
    label.ensurePolished()
    return label.font().pixelSize(), label.font().weight()


def _ladder_panel(qtbot) -> FormPanel:
    panel = FormPanel("Card Media")
    qtbot.addWidget(panel)
    panel.add_section("Sentence Audio")
    panel.add_field("Audio Format", QLineEdit())
    panel.add_section("Mappings", trailing=QCheckBox("Fill"))
    panel.show()
    return panel


@pytest.mark.usefixtures("themed")
def test_page_title_is_heading2_size_semibold(qtbot):
    panel = _ladder_panel(qtbot)

    assert _rendered(panel._title_label) == (FONT_SIZES.h2, 600)


@pytest.mark.usefixtures("themed")
@pytest.mark.parametrize("title", ["Sentence Audio", "Mappings"])
def test_section_heading_is_one_step_up_at_body_weight(qtbot, title):
    """Noticeably larger than body text, not bold (owner, item 12)."""
    panel = _ladder_panel(qtbot)
    heading = _find_section_label(panel, title)
    assert heading is not None, "Section heading QLabel not found"

    assert _rendered(heading) == (FONT_SIZES.h3, 400)


@pytest.mark.usefixtures("themed")
def test_field_label_stays_body_text(qtbot):
    panel = _ladder_panel(qtbot)
    label = _find_section_label(panel, "Audio Format:")
    assert label is not None, "Field label QLabel not found"

    assert _rendered(label) == (FONT_SIZES.body, 400)


def test_section_heading_size_follows_the_text_scale_token():
    """A literal px would not grow with the UI text scale; the h3 token does."""
    raw = (get_resource_dir() / "styles" / "common.qss").read_text(encoding="utf-8")
    # Sliced by hand: a rule body holds "${...}" braces, so a [^}]* regex stops early.
    start = raw.index("QLabel#settings-subheading {")
    body = raw[start : raw.index("\n}", start)]

    assert "font-size: ${font-size-h3}px;" in body

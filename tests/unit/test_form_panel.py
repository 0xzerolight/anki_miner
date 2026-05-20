"""Tests for FormPanel helper text rendering (Issue #20)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel

from anki_miner.gui.widgets.base.form_panel import FormPanel

_app = QApplication.instance() or QApplication([])


def _find_helper_label(panel: FormPanel, text_contains: str) -> QLabel | None:
    """Locate the QLabel whose text contains a substring."""
    for label in panel.findChildren(QLabel):
        if text_contains in label.text():
            return label
    return None


def test_helper_label_uses_plain_text_format():
    """Helper text must render as plain text so literal '<b>' markup is visible.

    Regression for Issue #20: QLabel defaults to AutoText, which detects
    HTML-looking strings and renders them as rich text. The bold-target
    helper contains literal '<b>...</b>' tokens that users need to see
    verbatim.
    """
    panel = FormPanel("Test Panel")
    helper_text = "Matches the Yomitan {cloze-prefix}<b>{cloze-body}</b>{cloze-suffix} idiom."
    panel.add_field("", QCheckBox("Bold target word"), helper=helper_text)

    helper_label = _find_helper_label(panel, "<b>{cloze-body}</b>")
    assert helper_label is not None, "Helper label with literal <b> markup not found"
    assert helper_label.textFormat() == Qt.TextFormat.PlainText
    # And the literal source text is preserved verbatim — no HTML stripping.
    assert helper_label.text() == helper_text


def test_helper_without_html_markup_still_plain_text():
    """Plain-prose helpers also use PlainText (consistent behavior)."""
    panel = FormPanel("Test Panel")
    panel.add_field("Label", QCheckBox(), helper="Plain helper text with no markup")

    helper_label = _find_helper_label(panel, "Plain helper text")
    assert helper_label is not None
    assert helper_label.textFormat() == Qt.TextFormat.PlainText

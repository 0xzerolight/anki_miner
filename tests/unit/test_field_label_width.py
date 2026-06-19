"""Tests for the shared label-column width helper and FileSelector wiring.

The folder/file path bars across tabs must line up: every labeled row in a
section shares one label-column width so the input fields start at the same x.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.widgets.base import field_label_width
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector


def test_no_texts_returns_zero(qapp):
    assert field_label_width() == 0


def test_single_text_is_positive(qapp):
    assert field_label_width("Subtitle Folder:") > 0


def test_longer_text_is_at_least_as_wide(qapp):
    short = field_label_width("Deck Name:")
    long = field_label_width("Subtitle Folder:")
    assert long >= short


def test_width_is_max_across_texts(qapp):
    longest = field_label_width("Subtitle Folder:")
    grouped = field_label_width("Subtitle Folder:", "Video Folder:", "Deck Name:")
    assert grouped == longest


def test_file_selector_uses_fixed_label_width(qtbot):
    w = FileSelector(label="Video File:", label_width=140)
    qtbot.addWidget(w)
    try:
        assert w.label is not None
        assert w.label.minimumWidth() == 140
        assert w.label.maximumWidth() == 140
    finally:
        w.deleteLater()


def test_file_selector_falls_back_to_minimum_width(qtbot):
    w = FileSelector(label="Video File:")
    qtbot.addWidget(w)
    try:
        assert w.label is not None
        assert w.label.minimumWidth() == 100
    finally:
        w.deleteLater()


def test_file_selector_empty_label_has_no_label_widget(qtbot):
    w = FileSelector(label="", label_width=140)
    qtbot.addWidget(w)
    try:
        assert w.label is None
    finally:
        w.deleteLater()


@pytest.mark.parametrize("texts", [("A:", "BB:"), ("Recent Files:", "Subtitle Offset:")])
def test_grouped_width_fits_every_member(qapp, texts):
    grouped = field_label_width(*texts)
    for t in texts:
        assert grouped >= field_label_width(t)

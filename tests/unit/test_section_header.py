"""Tests for SectionHeader layout (Issue: 'Add Series' button cut off).

The action button was kissing the card's right edge — its border was
clipped by the parent QFrame's border-radius. Adding a right margin to
the header layout gives the button breathing room.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.enhanced.section_header import SectionHeader


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_section_header_has_right_margin(qapp):
    """Right margin must be > 0 so the action button has breathing room."""
    header = SectionHeader(title="Multi-Anime Queue", action_text="Add Series")
    try:
        margins = header.layout().contentsMargins()
        assert margins.right() > 0
    finally:
        header.deleteLater()


def test_section_header_without_action_also_has_right_margin(qapp):
    """Right margin applies to all headers, not just those with action buttons."""
    header = SectionHeader(title="Section")
    try:
        margins = header.layout().contentsMargins()
        assert margins.right() > 0
    finally:
        header.deleteLater()

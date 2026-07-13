"""Tests for Issue #63 Task 5: Python-side font/row-height sites honoring ui_font_scale.

Covers the few spots that bypass QSS-driven scaling and were previously hardcoded:
- WordCurationDialog table row height + label fonts
- SubtitlePlayerWidget overlay font-size

All require a QApplication (QT_QPA_PLATFORM=offscreen in CI). The Theme singleton
font scale is reset to 1.0 in teardown so these tests never leak scale into others.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget


def _reset(font_scale: float = 1.0) -> None:
    """Reset the Theme singleton to a clean state at the given font scale."""
    Theme.initialize(
        active="light", favorites=("light", "dark"), user_dir=None, state_listener=None, font_scale=font_scale
    )


@pytest.fixture(autouse=True)
def _restore_scale():
    """Always reset the global font scale to 1.0 after each test."""
    yield
    _reset(1.0)


class TestCurationRowHeight:
    """WordCurationDialog row height scales with the global font scale."""

    def test_row_height_at_scale_1_0_equals_base(self, qtbot, make_tokenized_words):
        _reset(1.0)
        dlg = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dlg)
        try:
            vh = dlg.table.verticalHeader()
            assert vh is not None
            assert vh.defaultSectionSize() == WordCurationDialog._BASE_ROW_HEIGHT  # 32
        finally:
            dlg.deleteLater()

    def test_row_height_doubles_at_scale_2_0(self, qtbot, make_tokenized_words):
        _reset(2.0)
        dlg = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dlg)
        try:
            vh = dlg.table.verticalHeader()
            assert vh is not None
            assert vh.defaultSectionSize() == round(WordCurationDialog._BASE_ROW_HEIGHT * 2.0)  # 64
        finally:
            dlg.deleteLater()

    def test_make_font_scales_pixel_size(self, qtbot, make_tokenized_words):
        _reset(1.5)
        dlg = WordCurationDialog(make_tokenized_words(1))
        qtbot.addWidget(dlg)
        try:
            assert dlg._make_font(16).pixelSize() == 24  # 16 * 1.5
        finally:
            dlg.deleteLater()


class TestSubtitleOverlayFont:
    """SubtitlePlayerWidget overlay font-size scales with the global font scale."""

    def test_overlay_font_at_scale_1_0(self, qtbot):
        _reset(1.0)
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        try:
            assert "font-size: 18px" in widget.subtitle_label.styleSheet()
        finally:
            widget.deleteLater()

    def test_overlay_font_doubles_at_scale_2_0(self, qtbot):
        _reset(2.0)
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        try:
            assert "font-size: 36px" in widget.subtitle_label.styleSheet()
        finally:
            widget.deleteLater()

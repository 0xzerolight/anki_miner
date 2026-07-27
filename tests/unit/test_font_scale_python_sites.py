"""Tests for Issue #63 Task 5: Python-side font/row-height sites honoring ui_font_scale.

Covers the few spots that bypass QSS-driven scaling and were previously hardcoded:
- WordCurationDialog table row height + label fonts
- SubtitlePlayerWidget overlay font-size

All require a QApplication (QT_QPA_PLATFORM=offscreen in CI). The Theme singleton
font scale is reset to 1.0 in teardown so these tests never leak scale into others.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.resources.styles import FONT_SIZES
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.fonts import resolved_families
from anki_miner.gui.utils.qt_helpers import data_row_height
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget


def _reset(font_scale: float = 1.0) -> None:
    """Reset the Theme singleton to a clean state at the given font scale."""
    Theme.initialize(
        active="light", favorites=("light", "dark"), user_dir=None, state_listener=None, font_scale=font_scale
    )


@pytest.fixture(autouse=True)
def _restore_scale(qapp):
    """Reset the global font scale to 1.0 after each test, and put the
    application stylesheet back.

    ``Theme.apply_to_app`` is now needed here (row heights follow the *rendered*
    font, not a scale multiplier), and a leaked 2.0x stylesheet would override
    ``setFont`` in every later test on this worker.
    """
    stylesheet = qapp.styleSheet()
    yield
    _reset(1.0)
    qapp.setStyleSheet(stylesheet)


class TestCurationRowHeight:
    """WordCurationDialog row height comes from the shared data-surface rule.

    It used to be ``32 * Theme.get_font_scale()`` -- a constant that only
    tracked the scale because it multiplied by it, and that said nothing about
    the font actually being rendered. Decision D42 replaced it with one rule for
    every table, list and tree, so what is pinned here is that rule, and that
    the height still follows the applied text scale.
    """

    def test_row_height_is_the_shared_rule(self, qtbot, qapp, make_tokenized_words):
        _reset(1.0)
        Theme.apply_to_app(qapp)
        dlg = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dlg)
        try:
            vh = dlg.table.verticalHeader()
            assert vh is not None
            assert vh.defaultSectionSize() == data_row_height(dlg.table)
        finally:
            dlg.deleteLater()

    def test_row_height_grows_with_the_applied_scale(self, qtbot, qapp, make_tokenized_words):
        _reset(1.0)
        Theme.apply_to_app(qapp)
        small = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(small)
        baseline = small.table.verticalHeader().defaultSectionSize()

        _reset(2.0)
        Theme.apply_to_app(qapp)
        large = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(large)
        try:
            assert large.table.verticalHeader().defaultSectionSize() > baseline
        finally:
            small.deleteLater()
            large.deleteLater()

    def test_make_font_scales_pixel_size(self, qtbot, make_tokenized_words):
        _reset(1.5)
        dlg = WordCurationDialog(make_tokenized_words(1))
        qtbot.addWidget(dlg)
        try:
            assert dlg._make_font(16).pixelSize() == 24  # 16 * 1.5
        finally:
            dlg.deleteLater()


class TestSubtitleStripFont:
    """The subtitle strip is a font and a reserved height, not inline CSS.

    It used to carry an inline ``font-size: 18px`` stylesheet multiplied by the
    scale, on a label that was shown and hidden per cue. Decision D45-B made it
    Japanese content at the feature size, in a strip two lines tall for the
    whole session, so what is pinned now is the rendered font and the height it
    reserves.
    """

    def test_the_strip_uses_the_japanese_face_at_the_feature_size(self, qtbot):
        _reset(1.0)
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        try:
            font = widget.subtitle_strip.font()
            assert font.family() == resolved_families().japanese
            assert font.pixelSize() == FONT_SIZES.japanese_feature
        finally:
            widget.deleteLater()

    def test_the_reserved_two_lines_grow_with_the_applied_scale(self, qtbot):
        _reset(1.0)
        small = SubtitlePlayerWidget()
        qtbot.addWidget(small)
        baseline = small.subtitle_strip.height()

        _reset(2.0)
        large = SubtitlePlayerWidget()
        qtbot.addWidget(large)
        try:
            assert large.subtitle_strip.font().pixelSize() == 2 * FONT_SIZES.japanese_feature
            assert large.subtitle_strip.height() > baseline
        finally:
            small.deleteLater()
            large.deleteLater()

"""A content surface renders in ITS OWN language's face, not always Japanese.

The app stylesheet's ``QWidget { font-family: ${font-family-interface}; }`` rule
beats ``setFont`` for the family, and the ``*[japanese="body"]`` /
``*[japanese="feature"]`` blocks are the only rules specific enough to escape
it -- both of which pin the *Japanese* family. ``apply_content_font`` marks
every content surface with that same ``japanese`` property (the property name is
the stylesheet's, not a language claim), so before this fix a Chinese surface
was given the zh families by ``setFont`` and then had them overwritten by the
Japanese pin: every zh surface rendered in Japanese glyph shapes.

The two stylesheet declarations cannot simply be deleted -- that is what the
pre-existing ``test_the_japanese_rule_outranks_the_interface_rule``
(tests/unit/gui/test_fonts.py) pins, and without them the interface rule wins
and Japanese loses its face too. So the ja path is left exactly as it was and
the non-ja branch restates its families in a widget-level stylesheet, which
outranks the application one.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QPlainTextEdit

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.content_text import apply_content_font
from anki_miner.gui.utils.fonts import JAPANESE_FEATURE, resolved_families
from anki_miner.languages.registry import get_profile
from anki_miner.languages.zh.style import ZH_CONTENT_STYLE, ZH_FONT_FAMILIES

_COMMON_QSS = "styles", "common.qss"


def _under_the_theme(qapp, widget) -> None:
    """Polish *widget* with the real application stylesheet installed."""
    Theme.apply_to_app(qapp)
    widget.show()
    qapp.processEvents()


class TestNonJapaneseContentKeepsItsFamilies:
    """The defect: every zh surface used to come out in the Japanese face."""

    def test_a_zh_label_keeps_the_chinese_families(self, qapp, qtbot):
        label = QLabel("学习中文")
        qtbot.addWidget(label)
        apply_content_font(label, ZH_CONTENT_STYLE)
        saved = qapp.styleSheet()
        try:
            _under_the_theme(qapp, label)
            assert label.font().families() == list(ZH_FONT_FAMILIES)
            assert resolved_families().japanese not in label.font().families()
        finally:
            label.hide()
            qapp.setStyleSheet(saved)

    def test_a_zh_text_edit_keeps_them_too_at_feature_size(self, qapp, qtbot):
        """The paste box is the surface a user actually reads zh in."""
        edit = QPlainTextEdit("学习中文需要每天练习。")
        qtbot.addWidget(edit)
        apply_content_font(edit, ZH_CONTENT_STYLE, role=JAPANESE_FEATURE)
        saved = qapp.styleSheet()
        try:
            _under_the_theme(qapp, edit)
            assert edit.font().families() == list(ZH_FONT_FAMILIES)
            viewport = edit.viewport()
            assert viewport is not None
            # The viewport draws the text; the family has to reach it.
            assert viewport.font().families() == list(ZH_FONT_FAMILIES)
        finally:
            edit.hide()
            qapp.setStyleSheet(saved)

    def test_the_content_size_rule_still_applies(self, qapp, qtbot):
        """Restating the family must not cost the reading size."""
        from anki_miner.gui.resources.styles import FONT_SIZES

        plain = QLabel("chrome")
        marked = QLabel("学习")
        qtbot.addWidget(plain)
        qtbot.addWidget(marked)
        apply_content_font(marked, ZH_CONTENT_STYLE)
        saved = qapp.styleSheet()
        try:
            Theme.apply_to_app(qapp)
            plain.show()
            marked.show()
            qapp.processEvents()
            assert marked.font().pixelSize() > plain.font().pixelSize()
            assert marked.font().pixelSize() == FONT_SIZES.japanese_body
        finally:
            plain.hide()
            marked.hide()
            qapp.setStyleSheet(saved)


class TestJapaneseIsUntouched:
    """ja stability: the ja path is byte-identical, so its face must not move."""

    def test_a_ja_label_still_takes_the_japanese_face(self, qapp, qtbot):
        label = QLabel("日本語")
        qtbot.addWidget(label)
        apply_content_font(label, get_profile("ja").content_style)
        saved = qapp.styleSheet()
        try:
            _under_the_theme(qapp, label)
            assert label.font().family() == resolved_families().japanese
        finally:
            label.hide()
            qapp.setStyleSheet(saved)

    def test_the_ja_path_sets_no_widget_stylesheet(self, qtbot):
        """Only the non-ja branch restates a family; ja keeps the QSS pin."""
        label = QLabel()
        qtbot.addWidget(label)
        apply_content_font(label, get_profile("ja").content_style)
        assert label.styleSheet() == ""

    def test_the_japanese_qss_blocks_still_pin_the_japanese_family(self):
        """Deleting these two declarations would cost JAPANESE its face.

        ``QWidget { font-family: ${font-family-interface}; }`` matches every
        widget and beats ``setFont``; these attribute rules are the only thing
        more specific. Pinned so a later cleanup does not "simplify" them away.
        """
        source = (get_resource_dir() / _COMMON_QSS[0] / _COMMON_QSS[1]).read_text(encoding="utf-8")
        for role in ("body", "feature"):
            block = re.search(rf'\*\[japanese="{role}"\]\s*\{{(.*?)\n\}}', source, re.DOTALL)
            assert block is not None, role
            assert "font-family: ${font-family-japanese};" in block.group(1), role

"""Arabic consumes the RTL seam (contract C1-C4, C6) through profile data alone."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget

from anki_miner.gui.utils.content_text import apply_content_font
from anki_miner.languages.registry import get_profile


def test_the_profile_declares_rtl_and_the_bundled_naskh_face():
    profile = get_profile("ar")
    style = profile.content_style
    assert (style.direction, style.writing_system, style.bundled_fallback) == (
        "rtl",
        "Arabic",
        "NotoNaskhArabic-Regular.ttf",
    )
    assert "rtl" in profile.capabilities


def test_a_content_widget_flips_and_flips_back(qtbot):
    widget = QListWidget()
    qtbot.addWidget(widget)
    apply_content_font(widget, get_profile("ar").content_style)
    assert widget.layoutDirection() == Qt.LayoutDirection.RightToLeft
    apply_content_font(widget, get_profile("ko").content_style)
    assert widget.layoutDirection() == Qt.LayoutDirection.LeftToRight

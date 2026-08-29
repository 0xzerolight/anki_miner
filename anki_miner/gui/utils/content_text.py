"""Typography for surfaces that display MINED CONTENT, not interface chrome.

One owner for the three operations the eight content widgets need. ``font_role
== "japanese"`` routes into gui/utils/fonts.py unchanged, so a ja session is
byte-identical to the pre-multilanguage app; every other role builds from the
profile's own family list. Stage 2B consumes these three functions -- there is
no second helper on fonts.py.
"""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.resources.styles._variables import FONT_SIZES
from anki_miner.gui.utils.fonts import (
    JAPANESE_BODY,
    JAPANESE_FEATURE,
    JAPANESE_PROPERTY,
    apply_japanese_font,
    japanese_cell_font,
    make_scaled_font,
)
from anki_miner.languages.profile import ContentTextStyle

__all__ = ["apply_content_font", "content_cell_font", "content_phrase_wrap"]


def content_cell_font(style: ContentTextStyle) -> QFont:
    """A content face carrying no size, for table and list items."""
    if style.font_role == "japanese":
        return japanese_cell_font()
    font = QFont()
    font.setFamilies(list(style.families))
    return font


def apply_content_font(widget: QWidget, style: ContentTextStyle, *, role: str = JAPANESE_BODY) -> None:
    """Give *widget* the content face + size and mark it for the QSS rules."""
    if style.font_role == "japanese":
        apply_japanese_font(widget, role=role)
        return
    size = FONT_SIZES.japanese_feature if role == JAPANESE_FEATURE else FONT_SIZES.japanese_body
    font = make_scaled_font(size, QFont.Weight(widget.font().weight()))
    font.setFamilies(list(style.families))
    widget.setFont(font)
    # The property name stays "japanese": common.qss selects on it for every
    # content surface, and renaming it would be a stylesheet rewrite.
    widget.setProperty(JAPANESE_PROPERTY, role)
    qstyle = widget.style()
    if qstyle is not None:
        qstyle.unpolish(widget)
        qstyle.polish(widget)


def content_phrase_wrap(text: str, style: ContentTextStyle) -> str:
    """Soft-wrap *text* the way the language wants it (ja = BudouX phrases)."""
    return style.wrap(text)

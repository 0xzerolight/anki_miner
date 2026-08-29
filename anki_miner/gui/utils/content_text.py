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
        # A previous non-ja call pinned that language's families in a WIDGET
        # stylesheet, which outranks both the application sheet and setFont --
        # so a switch back to Japanese has to take it off again, or ja keeps
        # rendering in the outgoing face. Matched on the marker written below,
        # so a consumer's own stylesheet is never touched, and skipped entirely
        # on a widget that never left Japanese.
        if f'*[{JAPANESE_PROPERTY}="' in widget.styleSheet():
            widget.setStyleSheet("")
        apply_japanese_font(widget, role=role)
        return
    size = FONT_SIZES.japanese_feature if role == JAPANESE_FEATURE else FONT_SIZES.japanese_body
    font = make_scaled_font(size, QFont.Weight(widget.font().weight()))
    font.setFamilies(list(style.families))
    widget.setFont(font)
    # The property name stays "japanese": common.qss selects on it for every
    # content surface, and renaming it would be a stylesheet rewrite.
    widget.setProperty(JAPANESE_PROPERTY, role)
    # ...but those same rules pin the JAPANESE family, and a stylesheet beats
    # setFont for the family, so the setFamilies above would be overwritten and
    # Chinese would render in Japanese glyph shapes. The declarations cannot be
    # dropped -- ``QWidget { font-family: ${font-family-interface}; }`` matches
    # everything and would then win, costing Japanese its own face. A
    # widget-level stylesheet outranks the application one, so the non-ja
    # branch restates its families there. Scoped to the property so it lands on
    # this surface only; the size, colours and the rest of the theme still come
    # from the application sheet.
    families = ", ".join(f"'{name}'" for name in style.families)
    widget.setStyleSheet(f'*[{JAPANESE_PROPERTY}="{role}"] {{ font-family: {families}; }}')
    qstyle = widget.style()
    if qstyle is not None:
        qstyle.unpolish(widget)
        qstyle.polish(widget)


def content_phrase_wrap(text: str, style: ContentTextStyle) -> str:
    """Soft-wrap *text* the way the language wants it (ja = BudouX phrases)."""
    return style.wrap(text)

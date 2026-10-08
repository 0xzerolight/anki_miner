"""Typography for surfaces that display MINED CONTENT, not interface chrome.

One owner for what the mined-content surfaces need: a face, a soft-wrap and a
direction. ``font_role == "japanese"`` routes into gui/utils/fonts.py
unchanged, so a ja session is byte-identical to the pre-multilanguage app;
every other role builds from the profile's own family list. Stage 2B consumes
these functions -- there is no second helper on fonts.py.

A style's ``direction`` flips a content widget, never the chrome
(:func:`apply_content_direction`); a table that mixes content columns with
chrome columns gets :class:`ContentCellDelegate` on its content columns
instead, because flipping the view would mirror its columns. Its
``writing_system``/``bundled_fallback`` run through ``resolve_content_families``
(S21/S22); all of it is inert for a style that sets none.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from anki_miner.gui.resources.styles._variables import FONT_SIZES
from anki_miner.gui.utils.fonts import (
    JAPANESE_BODY,
    JAPANESE_FEATURE,
    JAPANESE_PROPERTY,
    apply_japanese_font,
    japanese_cell_font,
    make_scaled_font,
    resolve_content_families,
)
from anki_miner.languages.profile import ContentTextStyle

__all__ = [
    "ContentCellDelegate",
    "apply_content_direction",
    "apply_content_font",
    "content_cell_font",
    "content_phrase_wrap",
]


def _families_for(style: ContentTextStyle) -> tuple[str, ...]:
    """The style's families after the S22 probe; unchanged when it names no script."""
    return resolve_content_families(style.families, style.writing_system, style.bundled_fallback)


def apply_content_direction(widget: QWidget, style: ContentTextStyle) -> None:
    """Flip mined content right-to-left for an rtl language, and back (S21).

    Only the content widget flips, with the children it owns (a list's viewport
    and scrollbar); the application and its chrome stay left-to-right. Leaving
    rtl UNSETS the direction rather than forcing LeftToRight: the widget goes
    back to inheriting its parent's, the exact state of a widget that was never
    flipped. A widget that never was is not touched, so the ja and ltr paths
    make no Qt call here.

    Public for the lists whose every row is mined content (the curator's
    sentence picker, Known Words): they flip whole, like the subtitle viewer's
    line list. A table uses :class:`ContentCellDelegate` instead.
    """
    if style.direction == "rtl":
        widget.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    elif (
        widget.testAttribute(Qt.WidgetAttribute.WA_SetLayoutDirection)
        and widget.layoutDirection() == Qt.LayoutDirection.RightToLeft
    ):
        widget.unsetLayoutDirection()


def content_cell_font(style: ContentTextStyle) -> QFont:
    """A content face carrying no size, for table and list items."""
    if style.font_role == "japanese":
        return japanese_cell_font()
    font = QFont()
    font.setFamilies(list(_families_for(style)))
    return font


class ContentCellDelegate(QStyledItemDelegate):
    """Lay mined-content cells out in the mining language's direction.

    The cell counterpart of :func:`apply_content_direction`, for a table whose
    content columns sit beside chrome ones (counts, field names, timestamps):
    install it on the columns that carry :func:`content_cell_font`. Qt's item
    painter takes a cell's paragraph direction from the option, which the view
    fills from its own (left-to-right) chrome direction, so an rtl sentence
    was a left-to-right paragraph with its final punctuation at the wrong end.
    An rtl cell here is a right-to-left paragraph on the cell's leading (right)
    edge, and elides at its logical end, so its start stays in view.

    ``style`` is read at paint time: a screen whose mining language switches
    in-session passes ``lambda: self._content_style`` and never re-installs.
    An ltr style leaves the option exactly as the base built it, so ja and
    every ltr cell paint byte-identically.
    """

    def __init__(self, view: QAbstractItemView, style: Callable[[], ContentTextStyle]) -> None:
        super().__init__(view)
        self._style = style

    def initStyleOption(  # noqa: N802 - Qt override
        self, option: QStyleOptionViewItem | None, index: QModelIndex
    ) -> None:
        super().initStyleOption(option, index)
        if option is not None and self._style().direction == "rtl":
            option.direction = Qt.LayoutDirection.RightToLeft


def apply_content_font(widget: QWidget, style: ContentTextStyle, *, role: str = JAPANESE_BODY) -> None:
    """Give *widget* the content face + size and mark it for the QSS rules."""
    apply_content_direction(widget, style)
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
    families = _families_for(style)
    font = make_scaled_font(size, QFont.Weight(widget.font().weight()))
    font.setFamilies(list(families))
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
    declared = ", ".join(f"'{name}'" for name in families)
    widget.setStyleSheet(f'*[{JAPANESE_PROPERTY}="{role}"] {{ font-family: {declared}; }}')
    qstyle = widget.style()
    if qstyle is not None:
        qstyle.unpolish(widget)
        qstyle.polish(widget)


def content_phrase_wrap(text: str, style: ContentTextStyle) -> str:
    """Soft-wrap *text* the way the language wants it (ja = BudouX phrases)."""
    return style.wrap(text)

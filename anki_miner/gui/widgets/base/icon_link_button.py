"""Tool button that centres a small mark and its label, with clear space between them.

Qt's own TextBesideIcon layout (QCommonStyle ``CE_ToolButtonLabel``) packs
``[2px | icon | 2px | text]`` against the left edge. When a stylesheet rule pads the
button but keeps its native border, the padding only widens ``sizeHint`` and never moves
the content, so every spare pixel lands right of the label and the icon all but touches
the text. ``IconLinkButton`` keeps Qt's panel and hover painting and lays out the icon
and label itself.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QIcon, QPaintEvent, QPalette
from PyQt6.QtWidgets import QStyle, QStyleOptionToolButton, QStylePainter, QToolButton

_MARK_SIZE = QSize(16, 16)
_ICON_TEXT_GAP = 6


class IconLinkButton(QToolButton):
    """A QToolButton that draws ``[mark][gap][label]`` centred in its rect."""

    def set_mark(self, path: Path) -> None:
        """Show the image at ``path`` beside the label, or stay text-only if it won't load.

        Guard on the loaded icon (covers a missing OR unparseable SVG): a
        TextBesideIcon button with a null icon would leave a blank gap.
        """
        icon = QIcon(str(path))
        if icon.isNull():
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            return
        self.setIcon(icon)
        self.setIconSize(_MARK_SIZE)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def _lays_out_mark(self) -> bool:
        return not self.icon().isNull() and self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon

    def _content_width(self) -> int:
        return self.iconSize().width() + _ICON_TEXT_GAP + self.fontMetrics().horizontalAdvance(self.text())

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if not self._lays_out_mark():
            return hint
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        # The two space advances are the slack QToolButton gives a text-only label,
        # so the side padding matches the text-only buttons beside this one.
        contents = QSize(
            self._content_width() + 2 * self.fontMetrics().horizontalAdvance(" "),
            hint.height(),
        )
        style = self.style()
        assert style is not None
        width = style.sizeFromContents(QStyle.ContentsType.CT_ToolButton, opt, contents, self).width()
        return QSize(width, hint.height())

    def paintEvent(self, event: QPaintEvent | None) -> None:
        if not self._lays_out_mark():
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        # Panel and hover fill only; the mark and label are placed below.
        opt.text = ""
        opt.icon = QIcon()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, opt)

        mark = self.iconSize()
        x = (self.width() - self._content_width()) // 2
        mark_rect = QRect(x, (self.height() - mark.height()) // 2, mark.width(), mark.height())
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        self.icon().paint(painter, mark_rect, Qt.AlignmentFlag.AlignCenter, mode)

        text_left = mark_rect.right() + 1 + _ICON_TEXT_GAP
        text_rect = QRect(text_left, 0, self.width() - text_left, self.height())
        painter.drawItemText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            opt.palette,
            self.isEnabled(),
            self.text(),
            QPalette.ColorRole.ButtonText,
        )

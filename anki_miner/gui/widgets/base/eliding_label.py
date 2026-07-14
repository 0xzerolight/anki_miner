"""Single-line label that elides long text to its width and exposes the full text.

Compact list rows (the YouTube queue, file selectors) need to show text that can be
arbitrarily long — video titles, file paths, multi-line yt-dlp errors — without the
row clipping it mid-word or ballooning vertically. ``ElidingLabel`` collapses the text
to a single line, elides it to the current width with an ``…`` marker, and surfaces the
full original text in a tooltip on hover. It re-elides on resize, so widening the window
reveals more text.

Read the un-elided original via :attr:`full_text` rather than :meth:`text`, which
returns the (possibly truncated) displayed string.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFontMetrics, QResizeEvent
from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget

_WHITESPACE_RUN = re.compile(r"\s+")


class ElidingLabel(QLabel):
    """A single-line :class:`QLabel` that elides to its width and tooltips the full text.

    The displayed string has internal whitespace (including newlines) collapsed to
    single spaces so multi-line input stays one row. The tooltip preserves the original
    text verbatim. Use :attr:`full_text` to read the original.
    """

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        """Create the label.

        Args:
            text: Initial text.
            mode: Elision mode (``ElideRight`` for general text, ``ElideMiddle`` for
                paths/filenames where the tail matters).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._full_text = ""
        self._elide_mode = mode
        # Expand horizontally but never demand the full untruncated width — otherwise a
        # long line forces a horizontal scrollbar on the parent list instead of eliding.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        if text:
            self.setText(text)

    @property
    def full_text(self) -> str:
        """The full, un-elided original text (with original newlines/whitespace)."""
        return self._full_text

    def setText(self, text: str | None) -> None:  # noqa: N802 - Qt override
        """Store *text*, set a tooltip when it won't fit, and render it elided."""
        self._full_text = text or ""
        # Tooltip carries the verbatim original (newlines intact) so the user can read
        # the whole thing on hover. Cleared when the full text already fits.
        self._render()

    def _display_text(self) -> str:
        """The single-line form of the full text (whitespace runs collapsed)."""
        return _WHITESPACE_RUN.sub(" ", self._full_text).strip()

    def _render(self) -> None:
        display = self._display_text()
        available = max(self.width(), 1)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(display, self._elide_mode, available)
        super().setText(elided)
        # Show the tooltip only when something was actually hidden.
        if elided != display or display != self._full_text:
            self.setToolTip(self._full_text)
        else:
            self.setToolTip("")

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._render()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # Small minimum width so the label can shrink and elide instead of widening
        # its row — but keep QLabel's height, which includes any stylesheet
        # padding/min-height (bare font-metrics height clips styled captions).
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance("…"), super().minimumSizeHint().height())

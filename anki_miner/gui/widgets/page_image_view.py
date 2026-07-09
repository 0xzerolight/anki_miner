"""Fit-to-pane manga page preview with an optional block-highlight overlay.

Backs the word curation dialog's manga page pane: shows the page a focused
word came from and outlines the mokuro text block (speech bubble) containing
it. The page is painted at a fit-to-pane scale (aspect kept, no zoom); the
highlight is drawn *after* scaling with transformed coords so the outline
stays a crisp constant width at any zoom-out and resizing is repaint-only
(no per-resize pixmap copy of a ~20 MB decoded page).

:func:`load_page_qimage` is the off-thread loader (dispatch via
``run_off_thread``). It decodes with PIL — NOT ``QImage(path)`` — because the
app has no other Qt raster decode, so QImage-native jpeg/webp would be its
first dependency on Qt imageformats plugins: a bundle-only failure mode that
no unit test or release dry-run would catch. PIL is a hard dependency that
already decodes these exact page files on the card path
(``services/reading/images.prepare_card_image``).
"""

from __future__ import annotations

import io
import zipfile

from PIL import Image
from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.fonts import make_scaled_font
from anki_miner.services.reading.models import ImageRef

# Pre-filter on the DECLARED uncompressed member size before reading an
# archive page into memory. The declared size is attacker-controllable
# metadata; the hard bound is CPython zipfile's read-time size/CRC
# enforcement plus the bytes never touching disk (no zip-slip surface).
# This matches the project's documented local-user threat model
# (services/dictionary/zip_safety.py) — the full validate_zip_safe gate
# still runs on the card path before anything is written to disk.
_MAX_MEMBER_BYTES = 64 * 1024 * 1024

# Highlight styling: warm accent that reads on B/W manga art. Low-alpha fill
# so the bubble pops without hiding its text.
_HIGHLIGHT_COLOR = QColor(255, 80, 60)
_HIGHLIGHT_FILL_ALPHA = 35
_HIGHLIGHT_PEN_WIDTH = 2.5


def load_page_qimage(ref: ImageRef) -> QImage:
    """Decode ``ref`` to a full-resolution ``QImage`` (off-thread safe).

    Full-res is load-bearing: mokuro block boxes are in original-image pixel
    coords, so any downscale here would desync the highlight (this is also
    why ``prepare_card_image``'s 1280-cap disk JPEG is not reused). EXIF
    orientation is deliberately NOT applied — the boxes live in the raw
    decoded pixel space, same as the card path. PIL's default
    ``MAX_IMAGE_PIXELS`` decompression-bomb limit is deliberately inherited
    (the card path shares it); a trip raises and becomes the error
    placeholder while the card itself is unaffected.

    Raises on any failure (missing file, missing archive member, oversized
    declared member size, corrupt bytes) so callers route errors uniformly.
    """
    if ref.entry is None:
        with Image.open(ref.source) as img:
            return _to_qimage(img)
    with zipfile.ZipFile(ref.source) as zf:
        info = zf.getinfo(ref.entry)
        if info.file_size > _MAX_MEMBER_BYTES:
            raise ValueError(f"archive page {ref.entry!r} declares {info.file_size} bytes (cap {_MAX_MEMBER_BYTES})")
        data = zf.read(ref.entry)
    with Image.open(io.BytesIO(data)) as img:
        return _to_qimage(img)


def _to_qimage(img: Image.Image) -> QImage:
    """PIL image -> QImage via an RGBA byte buffer.

    ``convert("RGBA")`` normalizes every PIL mode (P/L/LA/CMYK/...) to a
    packed 4-bytes-per-pixel buffer. Format_RGBA8888 is byte-order R,G,B,A,
    matching PIL ``tobytes()``; Format_ARGB32 would swap R/B. The explicit
    ``w * 4`` stride documents the packed-buffer invariant, and ``.copy()``
    detaches the QImage from the Python buffer before it goes out of scope.
    """
    rgba = img.convert("RGBA")
    width, height = rgba.size
    qimage = QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    if qimage.isNull():
        raise ValueError("decoded page produced a null QImage")
    return qimage


class PageImageView(QWidget):
    """Composite pane: fit-to-pane page canvas over a small caption label.

    API is state-setting only — :meth:`show_page`, :meth:`show_message`,
    :meth:`clear` — with all drawing in the canvas ``paintEvent`` so a
    resize refits without touching the stored pixmap.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        self._canvas = _PageCanvas(self)
        layout.addWidget(self._canvas, 1)

        self._caption = QLabel()
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption.setFont(make_scaled_font(11))
        layout.addWidget(self._caption)

    def show_page(self, pixmap: QPixmap, box: tuple[int, int, int, int] | None, caption: str) -> None:
        """Show ``pixmap`` with an optional block highlight and page caption."""
        self._canvas.set_content(pixmap, box, message="")
        self._caption.setText(caption)

    def show_message(self, text: str, caption: str = "") -> None:
        """Show a centered placeholder message instead of a page."""
        self._canvas.set_content(None, None, message=text)
        self._caption.setText(caption)

    def clear(self) -> None:
        """Empty the pane (no page, no message, no caption)."""
        self._canvas.set_content(None, None, message="")
        self._caption.setText("")

    # Exposed for tests.
    @property
    def current_pixmap(self) -> QPixmap | None:
        return self._canvas.pixmap

    @property
    def current_box(self) -> tuple[int, int, int, int] | None:
        return self._canvas.box

    @property
    def current_message(self) -> str:
        return self._canvas.message

    @property
    def caption_text(self) -> str:
        return self._caption.text()


class _PageCanvas(QWidget):
    """The painted surface: page at fit scale + highlight, or a message."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(120, 160)
        self.pixmap: QPixmap | None = None
        self.box: tuple[int, int, int, int] | None = None
        self.message: str = ""

    def set_content(
        self,
        pixmap: QPixmap | None,
        box: tuple[int, int, int, int] | None,
        *,
        message: str,
    ) -> None:
        self.pixmap = pixmap
        self.box = box
        self.message = message
        self.update()

    @staticmethod
    def fit_transform(pane_w: float, pane_h: float, img_w: float, img_h: float) -> tuple[float, float, float]:
        """(scale, dx, dy) that fits an ``img_w x img_h`` page centered in the pane.

        Pure math, factored out for direct testing. Aspect is kept; a page
        smaller than the pane is scaled up to fit (fit-to-pane, not
        shrink-only). Degenerate sizes yield a zero scale so callers skip
        drawing.
        """
        if img_w <= 0 or img_h <= 0 or pane_w <= 0 or pane_h <= 0:
            return 0.0, 0.0, 0.0
        scale = min(pane_w / img_w, pane_h / img_h)
        dx = (pane_w - img_w * scale) / 2
        dy = (pane_h - img_h * scale) / 2
        return scale, dx, dy

    @staticmethod
    def clamped_box(box: tuple[int, int, int, int], img_w: int, img_h: int) -> QRect:
        """``box`` intersected with the page rect (out-of-bounds boxes exist)."""
        xmin, ymin, xmax, ymax = box
        return QRect(xmin, ymin, xmax - xmin, ymax - ymin).intersected(QRect(0, 0, img_w, img_h))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            if self.message:
                painter.setPen(QColor(128, 128, 128))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
                return
            if self.pixmap is None or self.pixmap.isNull():
                return

            img_w, img_h = self.pixmap.width(), self.pixmap.height()
            scale, dx, dy = self.fit_transform(self.width(), self.height(), img_w, img_h)
            if scale <= 0:
                return
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            target = QRectF(dx, dy, img_w * scale, img_h * scale)
            painter.drawPixmap(target, self.pixmap, QRectF(0, 0, img_w, img_h))

            if self.box is None:
                return
            clamped = self.clamped_box(self.box, img_w, img_h)
            if clamped.isEmpty():
                return
            # Highlight AFTER scaling with transformed coords: constant crisp
            # pen width at any fit scale, no full-page pixmap copy needed.
            highlight = QRectF(
                dx + clamped.x() * scale,
                dy + clamped.y() * scale,
                clamped.width() * scale,
                clamped.height() * scale,
            )
            fill = QColor(_HIGHLIGHT_COLOR)
            fill.setAlpha(_HIGHLIGHT_FILL_ALPHA)
            painter.fillRect(highlight, fill)
            painter.setPen(QPen(_HIGHLIGHT_COLOR, _HIGHLIGHT_PEN_WIDTH))
            painter.drawRect(highlight)
        finally:
            painter.end()

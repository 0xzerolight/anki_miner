"""Tests for PageImageView and its off-thread page loader.

Covers the fit/clamp math (pure static helpers), the show_page/show_message/
clear state machine, and ``load_page_qimage`` across the container formats
mokuro volumes actually ship (PNG/JPEG/WebP, dir-backed and zip-backed),
including the RGBA byte-order pixel check and the error paths.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError
from PyQt6.QtGui import QColor, QImage, QPixmap

from anki_miner.gui.widgets import page_image_view as piv
from anki_miner.gui.widgets.page_image_view import PageImageView, _PageCanvas, load_page_qimage
from anki_miner.services.reading.models import ImageRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_image(path: Path, fmt: str, size=(8, 6), color=(10, 20, 30)) -> Path:
    """Synthesize a small image with PIL (the same decoder under test)."""
    Image.new("RGB", size, color).save(path, fmt)
    return path


def _png_bytes(size=(4, 4), color=(1, 2, 3)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _pixmap(width: int = 100, height: int = 200) -> QPixmap:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(255, 255, 255))
    return QPixmap.fromImage(image)


# ---------------------------------------------------------------------------
# Fit / clamp math (pure)
# ---------------------------------------------------------------------------


class TestFitTransform:
    def test_wide_pane_fits_by_height(self):
        scale, dx, dy = _PageCanvas.fit_transform(1000, 100, 100, 100)
        assert scale == 1.0
        assert dx == 450.0
        assert dy == 0.0

    def test_tall_pane_fits_by_width(self):
        scale, dx, dy = _PageCanvas.fit_transform(100, 1000, 200, 200)
        assert scale == 0.5
        assert dx == 0.0
        assert dy == 450.0

    def test_small_page_scales_up_to_fit(self):
        scale, _, _ = _PageCanvas.fit_transform(400, 400, 100, 100)
        assert scale == 4.0

    def test_degenerate_sizes_yield_zero_scale(self):
        assert _PageCanvas.fit_transform(100, 100, 0, 50) == (0.0, 0.0, 0.0)
        assert _PageCanvas.fit_transform(0, 100, 50, 50) == (0.0, 0.0, 0.0)


class TestClampedBox:
    def test_in_bounds_box_unchanged(self):
        rect = _PageCanvas.clamped_box((10, 20, 60, 90), 100, 100)
        assert (rect.x(), rect.y(), rect.width(), rect.height()) == (10, 20, 50, 70)

    def test_out_of_bounds_box_clamped_to_page(self):
        rect = _PageCanvas.clamped_box((-50, -50, 99999, 99999), 800, 1200)
        assert (rect.x(), rect.y(), rect.width(), rect.height()) == (0, 0, 800, 1200)

    def test_fully_outside_box_is_empty(self):
        rect = _PageCanvas.clamped_box((900, 900, 950, 950), 800, 800)
        assert rect.isEmpty()


# ---------------------------------------------------------------------------
# Widget state machine
# ---------------------------------------------------------------------------


class TestPageImageView:
    def test_show_page_sets_pixmap_box_caption(self, qtbot):
        view = PageImageView()
        qtbot.addWidget(view)
        view.show_page(_pixmap(), (1, 2, 3, 4), "p.7")
        assert view.current_pixmap is not None
        assert view.current_box == (1, 2, 3, 4)
        assert view.current_message == ""
        assert view.caption_text == "p.7"

    def test_show_message_clears_pixmap(self, qtbot):
        view = PageImageView()
        qtbot.addWidget(view)
        view.show_page(_pixmap(), None, "p.1")
        view.show_message("nothing here", "p.2")
        assert view.current_pixmap is None
        assert view.current_message == "nothing here"
        assert view.caption_text == "p.2"

    def test_clear_empties_everything(self, qtbot):
        view = PageImageView()
        qtbot.addWidget(view)
        view.show_page(_pixmap(), (0, 0, 1, 1), "p.3")
        view.clear()
        assert view.current_pixmap is None
        assert view.current_box is None
        assert view.current_message == ""
        assert view.caption_text == ""

    def test_paints_without_error(self, qtbot):
        # Render each state into an off-screen pixmap: exercises paintEvent
        # (page + highlight, clamped box, message) without a window system.
        view = PageImageView()
        qtbot.addWidget(view)
        view.resize(300, 400)
        for state in ("page", "message", "empty"):
            if state == "page":
                view.show_page(_pixmap(), (-5, -5, 500, 500), "p.1")
            elif state == "message":
                view.show_message("placeholder")
            else:
                view.clear()
            view._canvas.grab()  # forces a real paintEvent pass


# ---------------------------------------------------------------------------
# load_page_qimage — dir-backed pages
# ---------------------------------------------------------------------------


class TestLoadDirPage:
    @pytest.mark.parametrize("fmt,ext", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")])
    def test_decodes_common_formats(self, qapp, tmp_path, fmt, ext):
        path = _write_image(tmp_path / f"page.{ext}", fmt)
        image = load_page_qimage(ImageRef(path))
        assert not image.isNull()
        assert (image.width(), image.height()) == (8, 6)

    def test_pixel_colors_round_trip(self, qapp, tmp_path):
        # Locks the Format_RGBA8888 <-> PIL byte order: a silent R/B swap
        # (e.g. Format_ARGB32) would pass every size/non-null assertion.
        img = Image.new("RGB", (4, 4), (0, 255, 0))
        img.putpixel((0, 0), (255, 0, 0))
        img.putpixel((3, 3), (0, 0, 255))
        path = tmp_path / "page.png"
        img.save(path, "PNG")  # lossless, so exact colors survive

        image = load_page_qimage(ImageRef(path))
        assert image.pixelColor(0, 0) == QColor(255, 0, 0)
        assert image.pixelColor(3, 3) == QColor(0, 0, 255)
        assert image.pixelColor(1, 1) == QColor(0, 255, 0)

    def test_palette_mode_normalized(self, qapp, tmp_path):
        # Real manga scans include palette ('P') PNGs; convert("RGBA") must
        # normalize them to the packed buffer the QImage constructor expects.
        path = tmp_path / "palette.png"
        Image.new("RGB", (5, 5), (200, 100, 50)).convert("P", palette=Image.Palette.ADAPTIVE).save(path, "PNG")
        image = load_page_qimage(ImageRef(path))
        assert not image.isNull()
        assert image.pixelColor(2, 2) == QColor(200, 100, 50)

    def test_missing_file_raises(self, qapp, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_page_qimage(ImageRef(tmp_path / "gone.png"))

    def test_corrupt_file_raises(self, qapp, tmp_path):
        path = tmp_path / "broken.png"
        path.write_bytes(b"not an image at all")
        with pytest.raises(UnidentifiedImageError):
            load_page_qimage(ImageRef(path))


# ---------------------------------------------------------------------------
# load_page_qimage — archive-backed pages
# ---------------------------------------------------------------------------


class TestLoadArchivePage:
    def test_zip_member_decodes(self, qapp, tmp_path):
        archive = tmp_path / "vol.cbz"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("pages/001.png", _png_bytes())
        image = load_page_qimage(ImageRef(archive, "pages/001.png"))
        assert not image.isNull()
        assert (image.width(), image.height()) == (4, 4)

    def test_missing_member_raises(self, qapp, tmp_path):
        archive = tmp_path / "vol.cbz"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("001.png", _png_bytes())
        with pytest.raises(KeyError):
            load_page_qimage(ImageRef(archive, "002.png"))

    def test_corrupt_member_raises(self, qapp, tmp_path):
        archive = tmp_path / "vol.cbz"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("001.png", b"garbage bytes")
        with pytest.raises(UnidentifiedImageError):
            load_page_qimage(ImageRef(archive, "001.png"))

    def test_oversized_declared_member_raises(self, qapp, tmp_path, monkeypatch):
        archive = tmp_path / "vol.cbz"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("001.png", _png_bytes())
        monkeypatch.setattr(piv, "_MAX_MEMBER_BYTES", 10)
        with pytest.raises(ValueError, match="declares"):
            load_page_qimage(ImageRef(archive, "001.png"))

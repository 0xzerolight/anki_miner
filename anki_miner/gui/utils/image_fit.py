"""Fit-to-pane image geometry shared by the manga page view and the Video OCR region dialog."""

from __future__ import annotations

from PyQt6.QtCore import QRect


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


def clamped_box(box: tuple[int, int, int, int], img_w: int, img_h: int) -> QRect:
    """``box`` intersected with the page rect (out-of-bounds boxes exist)."""
    xmin, ymin, xmax, ymax = box
    if img_w <= 0 or img_h <= 0 or xmin >= xmax or ymin >= ymax:
        return QRect()
    left = min(max(xmin, 0), img_w)
    top = min(max(ymin, 0), img_h)
    right = min(max(xmax, 0), img_w)
    bottom = min(max(ymax, 0), img_h)
    if left >= right or top >= bottom:
        return QRect()
    return QRect(left, top, right - left, bottom - top)


def widget_to_image(px: float, py: float, scale: float, dx: float, dy: float) -> tuple[float, float]:
    """Inverse of the fit transform: a widget point to image pixel coordinates."""
    return (px - dx) / scale, (py - dy) / scale

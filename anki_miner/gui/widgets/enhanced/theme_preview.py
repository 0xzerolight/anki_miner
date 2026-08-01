"""Off-screen theme thumbnails for the theme gallery.

Renders a miniature of the real application UI wearing a given theme WITHOUT
touching ``QApplication``. Two reasons that constraint is absolute:

* Qt has no "un-set" for an application palette. A preview routed through
  ``Theme.apply_to_app`` leaves every widget built afterwards explicitly styled,
  even after a faithful save/restore.
* Each application-scoped apply costs a measured ~1647 ms whole-app repolish on
  the GUI thread. Twenty-nine of those is not a gallery, it is a hang.

Widget-scoped ``setPalette`` + ``setStyleSheet`` renders identically -- verified
pixel for pixel in ``tests/unit/gui/test_theme_gallery.py`` -- and dies with the
widget.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles._variables import SPACING
from anki_miner.gui.resources.styles.theme import Theme

from .modern_button import ModernButton

#: Card thumbnail size in logical pixels.
DEFAULT_THUMBNAIL_SIZE = QSize(240, 160)

#: Build the mock at this multiple of the target size, then scale down. The
#: app's font sizes are absolute pixels baked into the compiled QSS, so a 1x
#: render produces full-size text with no room for anything around it. At 2x the
#: same widgets read as a miniature of the real window.
_RENDER_SCALE = 2

#: (theme_key, width, height, font_scale) -> pixmap. The font scale is part of
#: the key rather than an invalidation hook, so a scale change simply misses
#: instead of coupling this module to ``Theme.set_font_scale``.
_cache: dict[tuple[str, int, int, float], QPixmap] = {}


def clear_thumbnail_cache() -> None:
    """Drop every cached thumbnail.

    Call after anything that can redefine what a theme *key* means -- a profile
    switch or any other ``Theme.initialize``, since a user JSON file can shadow
    a shipped theme under the same key.
    """
    _cache.clear()


def _build_mock(theme_key: str, size: QSize) -> QWidget:
    """Build an unmapped widget wearing ``theme_key`` and nothing else."""
    page = QWidget()
    # Laid out and polished, never mapped to the screen: no flash, no window.
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.resize(size)

    layout = QVBoxLayout(page)
    layout.setContentsMargins(SPACING.sm, SPACING.sm, SPACING.sm, SPACING.sm)
    layout.setSpacing(SPACING.xs)

    title = QLabel("Anki Miner")
    title.setObjectName("caption")
    layout.addWidget(title)

    row = QHBoxLayout()
    row.setSpacing(SPACING.xs)
    row.addWidget(ModernButton("Mine", variant="primary"))
    disabled = ModernButton("Off", variant="secondary")
    disabled.setEnabled(False)
    row.addWidget(disabled)
    row.addStretch(1)
    layout.addLayout(row)

    rows = QListWidget()
    rows.addItems(["食べる  taberu", "見る  miru", "走る  hashiru"])
    rows.setCurrentRow(0)
    layout.addWidget(rows, 1)

    # Palette first, then the sheet. Same order (and same reason) as
    # Theme.apply_to_app: a stylesheet freezes a polished widget's palette, so a
    # palette written afterwards would not reach anything.
    page.setPalette(Theme.build_palette(theme_key))
    page.setStyleSheet(Theme.get_stylesheet(theme_key))
    return page


def render_theme_thumbnail(theme_key: str, size: QSize = DEFAULT_THUMBNAIL_SIZE) -> QPixmap:
    """Return a cached miniature of the app rendered in ``theme_key``.

    Args:
        theme_key: Theme key, e.g. ``"catppuccin-mocha"``. An unknown key
            renders whatever ``Theme.get_colors`` falls back to rather than
            raising -- a gallery card naming a theme that left the disk should
            look wrong, not crash the panel.
        size: Logical size of the returned pixmap.
    """
    cache_key = (theme_key, size.width(), size.height(), Theme.get_font_scale())
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    render_size = QSize(size.width() * _RENDER_SCALE, size.height() * _RENDER_SCALE)
    mock = _build_mock(theme_key, render_size)
    try:
        mock.show()  # WA_DontShowOnScreen: polishes and lays out, never maps.
        mock.ensurePolished()
        pixmap = mock.grab().scaled(
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    finally:
        mock.setParent(None)
        mock.deleteLater()

    _cache[cache_key] = pixmap
    return pixmap

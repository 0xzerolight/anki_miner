"""Off-screen theme thumbnails: render, cache, and application isolation."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPalette

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.enhanced.theme_preview import (
    DEFAULT_THUMBNAIL_SIZE,
    clear_thumbnail_cache,
    render_theme_thumbnail,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_thumbnail_cache()
    yield
    clear_thumbnail_cache()


class TestRendering:
    def test_every_shipped_theme_renders(self, qapp):
        failures = []
        for key in sorted(Theme.get_available_themes()):
            try:
                pixmap = render_theme_thumbnail(key)
                if pixmap.isNull() or pixmap.width() <= 0:
                    failures.append(f"{key}: null pixmap")
            except Exception as exc:  # noqa: BLE001 - the point is which theme
                failures.append(f"{key}: {type(exc).__name__}: {exc}")
        assert not failures, "themes failed to render:\n" + "\n".join(failures)

    def test_pixmap_matches_requested_size(self, qapp):
        size = QSize(120, 80)
        pixmap = render_theme_thumbnail("dark", size)
        assert (pixmap.width(), pixmap.height()) == (120, 80)

    def test_two_themes_render_different_pixels(self, qapp):
        light = render_theme_thumbnail("light").toImage()
        mocha = render_theme_thumbnail("catppuccin-mocha").toImage()
        assert light != mocha

    def test_unknown_key_does_not_raise(self, qapp):
        # Theme.get_colors falls back to the first discovered theme for an
        # unknown key; the renderer must inherit that leniency, not crash a
        # gallery because one card names a theme that vanished from disk.
        assert not render_theme_thumbnail("no-such-theme").isNull()


class TestApplicationIsolation:
    def test_rendering_never_touches_the_application(self, qapp):
        """The whole feature rests on this: a leak here is process-global."""
        before_sheet = qapp.styleSheet()
        before_window = qapp.palette().color(QPalette.ColorRole.Window).name()
        for key in sorted(Theme.get_available_themes()):
            render_theme_thumbnail(key)
        assert qapp.styleSheet() == before_sheet
        assert qapp.palette().color(QPalette.ColorRole.Window).name() == before_window


class TestCache:
    def test_repeat_call_returns_the_cached_pixmap(self, qapp):
        first = render_theme_thumbnail("nord")
        second = render_theme_thumbnail("nord")
        assert first is second

    def test_font_scale_is_part_of_the_cache_key(self, qapp):
        first = render_theme_thumbnail("nord")
        Theme.set_font_scale(1.5)
        try:
            second = render_theme_thumbnail("nord")
        finally:
            Theme.set_font_scale(1.0)
        assert first is not second

    def test_clear_drops_everything(self, qapp):
        first = render_theme_thumbnail("nord")
        clear_thumbnail_cache()
        assert render_theme_thumbnail("nord") is not first

    def test_size_is_part_of_the_cache_key(self, qapp):
        a = render_theme_thumbnail("nord", DEFAULT_THUMBNAIL_SIZE)
        b = render_theme_thumbnail("nord", QSize(100, 60))
        assert a is not b

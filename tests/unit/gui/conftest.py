"""Fixtures shared by the GUI layout tests.

``Theme.apply_to_app`` writes the application stylesheet and palette, which are
process-global and outlive the test that set them. Restoring only the *scale*
leaves the stylesheet installed, and a QSS ``font-size`` rule then overrides any
per-widget ``setFont`` -- which is exactly how a layout test in one module made
``test_sizing_metrics.py`` fail in another. Anything that raises the text scale
must go through :func:`font_scale`, which puts the application back byte for
byte.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QPalette

from anki_miner.gui.resources.styles.theme import Theme


@pytest.fixture
def font_scale(qapp):
    """Yield ``apply(scale)``, rebuilding the theme at that UI text scale.

    ``apply_to_app`` is load-bearing: ``set_font_scale`` alone only invalidates
    the compiled-QSS cache, so a widget's fontMetrics never changes and a test
    that skips it silently measures nothing.
    """
    original_scale = Theme.get_font_scale()
    original_stylesheet = qapp.styleSheet()
    original_palette = QPalette(qapp.palette())

    def apply(scale: float) -> None:
        Theme.set_font_scale(scale)
        Theme.apply_to_app(qapp)

    yield apply

    Theme.set_font_scale(original_scale)
    qapp.setStyleSheet(original_stylesheet)
    qapp.setPalette(original_palette)

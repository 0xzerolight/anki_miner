"""Regression tests for the dark-mode Word Preview alternating-row fix.

Covers two independent failure paths that combined to produce the v2.3.1 bug
where every other row in the Word Preview dialog rendered with a bright white
background under dark mode:

1. common.qss missed ``QTableWidget::item:alternate`` selectors, so alternating
   rows fell back to the platform-default AlternateBase color.
2. Theme.apply_to_app only set the ``Window`` and ``WindowText`` palette roles,
   leaving ``Base``, ``AlternateBase``, and ``Text`` at their OS defaults.

These tests pin both pieces of the fix so a future refactor that drops either
one surfaces in CI instead of in a user-reported screenshot.
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.resources.styles.theme import Theme

# QApplication instance needed to exercise apply_to_app.
_app = QApplication.instance() or QApplication([])


class TestAlternatingRowStylesheet:
    """The common.qss rules must cover QTableWidget alternating rows."""

    def test_stylesheet_defines_alternate_item_rule(self):
        qss = Theme.get_stylesheet("dark")
        assert "QTableWidget::item:alternate" in qss

    def test_stylesheet_defines_alternate_hover_rule(self):
        qss = Theme.get_stylesheet("dark")
        assert "QTableWidget::item:alternate:hover" in qss

    def test_stylesheet_defines_alternate_selected_rule(self):
        qss = Theme.get_stylesheet("dark")
        assert "QTableWidget::item:alternate:selected" in qss

    def test_alternate_rule_uses_surface_alt_color(self):
        """The alternate row color must resolve to the theme's surface-alt token."""
        dark_qss = Theme.get_stylesheet("dark")
        light_qss = Theme.get_stylesheet("light")

        assert Theme.get_colors("dark")["surface-alt"] in dark_qss
        assert Theme.get_colors("light")["surface-alt"] in light_qss


class TestApplyToAppPalette:
    """Theme.apply_to_app must set the palette roles Qt's native row renderer consults."""

    def test_alternate_base_matches_surface_alt(self):
        Theme.apply_to_app(_app, "dark")
        palette = _app.palette()
        expected = QColor(Theme.get_colors("dark")["surface-alt"])
        assert palette.color(QPalette.ColorRole.AlternateBase) == expected

    def test_base_matches_surface(self):
        Theme.apply_to_app(_app, "dark")
        palette = _app.palette()
        expected = QColor(Theme.get_colors("dark")["surface"])
        assert palette.color(QPalette.ColorRole.Base) == expected

    def test_text_role_is_set(self):
        Theme.apply_to_app(_app, "dark")
        palette = _app.palette()
        expected = QColor(Theme.get_colors("dark")["text"])
        assert palette.color(QPalette.ColorRole.Text) == expected

    def test_light_mode_palette_switch(self):
        """Switching modes must update AlternateBase, not leave it stuck on dark."""
        Theme.apply_to_app(_app, "dark")
        Theme.apply_to_app(_app, "light")
        palette = _app.palette()
        expected = QColor(Theme.get_colors("light")["surface-alt"])
        assert palette.color(QPalette.ColorRole.AlternateBase) == expected

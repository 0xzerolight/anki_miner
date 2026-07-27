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

import re

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.resources.styles.theme import Theme

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rule_body(qss: str, selector: str) -> str:
    """Return every declaration written for exactly ``selector``.

    QSS has no cascade to resolve here: the same selector may appear in the
    structural block and again in the theme block, and both apply. Substring
    matching would not do -- ``"QTableWidget"`` also occurs inside
    ``QTableWidget::item:selected``.
    """
    bodies = [
        body
        for group, body in _RULE.findall(_COMMENT.sub("", qss))
        if selector in [s.strip() for s in group.split(",")]
    ]
    assert bodies, f"no rule found for {selector}"
    return "\n".join(bodies)


@pytest.fixture(autouse=True)
def _clear_app_stylesheet():
    """``Theme.apply_to_app`` sets a stylesheet *and a palette* on the shared
    QApplication. Put both back in teardown so this file does not leak a theme
    into later tests that read widget font metrics, row heights or colours.
    """
    app = QApplication.instance()
    palette = QPalette(app.palette()) if isinstance(app, QApplication) else None
    yield
    if isinstance(app, QApplication):
        app.setStyleSheet("")
        if palette is not None:
            app.setPalette(palette)


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

    def test_alternate_base_matches_surface_alt(self, qapp):
        Theme.apply_to_app(qapp, "dark")
        palette = qapp.palette()
        expected = QColor(Theme.get_colors("dark")["surface-alt"])
        assert palette.color(QPalette.ColorRole.AlternateBase) == expected

    def test_base_matches_surface(self, qapp):
        Theme.apply_to_app(qapp, "dark")
        palette = qapp.palette()
        expected = QColor(Theme.get_colors("dark")["surface"])
        assert palette.color(QPalette.ColorRole.Base) == expected

    def test_text_role_is_set(self, qapp):
        Theme.apply_to_app(qapp, "dark")
        palette = qapp.palette()
        expected = QColor(Theme.get_colors("dark")["text"])
        assert palette.color(QPalette.ColorRole.Text) == expected

    def test_light_mode_palette_switch(self, qapp):
        """Switching modes must update AlternateBase, not leave it stuck on dark."""
        Theme.apply_to_app(qapp, "dark")
        Theme.apply_to_app(qapp, "light")
        palette = qapp.palette()
        expected = QColor(Theme.get_colors("light")["surface-alt"])
        assert palette.color(QPalette.ColorRole.AlternateBase) == expected


# ---------------------------------------------------------------------------
# D42: one selection, whichever kind of data view is under the pointer
# ---------------------------------------------------------------------------

#: Light, dark, and a shipped theme neither of them derives from.
_THEMES = ("light", "dark", "nord")


class TestOneSelectionAcrossEveryDataView:
    """A list took the OS selection colour while a table beside it took the
    theme's, so one screen showed two different-looking selections."""

    @pytest.mark.parametrize("mode", _THEMES)
    def test_lists_are_styled_like_tables_and_trees(self, mode):
        qss = Theme.get_stylesheet(mode)

        for selector in (
            "QListWidget::item:hover",
            "QListWidget::item:selected",
            "QListWidget::item:alternate",
        ):
            assert selector in qss, f"{mode}: missing {selector}"

    @pytest.mark.parametrize("mode", _THEMES)
    def test_every_view_selects_in_the_theme_colours(self, mode):
        colors = Theme.get_colors(mode)
        qss = Theme.get_stylesheet(mode)

        for view in ("QTableWidget", "QListWidget", "QTreeWidget"):
            rule = _rule_body(qss, f"{view}::item:selected")
            assert colors["table-selected-bg"] in rule, f"{mode}: {view} selection background"
            assert colors["table-selected-text"] in rule, f"{mode}: {view} selection text"

    @pytest.mark.parametrize("mode", _THEMES)
    def test_every_view_hovers_in_the_theme_colours(self, mode):
        colors = Theme.get_colors(mode)
        qss = Theme.get_stylesheet(mode)

        for view in ("QTableWidget", "QListWidget", "QTreeWidget"):
            rule = _rule_body(qss, f"{view}::item:hover")
            assert colors["surface-hover"] in rule, f"{mode}: {view} hover"

    def test_the_transparent_selection_override_is_gone(self):
        """``selection-background-color: transparent`` contradicted the item rule."""
        rule = _rule_body(Theme.get_stylesheet("dark"), "QTableWidget")

        assert "selection-background-color" not in rule


class TestSelectionPaletteRoles:
    """Rows drawn by Qt rather than by QSS -- an embedded row widget, a popup --
    read the selection from the palette, so it must carry the same two colours."""

    @pytest.mark.parametrize("mode", _THEMES)
    def test_highlight_matches_the_selection_background(self, qapp, mode):
        Theme.apply_to_app(qapp, mode)

        expected = QColor(Theme.get_colors(mode)["table-selected-bg"])
        assert qapp.palette().color(QPalette.ColorRole.Highlight) == expected

    @pytest.mark.parametrize("mode", _THEMES)
    def test_highlighted_text_matches_the_selection_text(self, qapp, mode):
        Theme.apply_to_app(qapp, mode)

        expected = QColor(Theme.get_colors(mode)["table-selected-text"])
        assert qapp.palette().color(QPalette.ColorRole.HighlightedText) == expected

    def test_switching_themes_moves_the_highlight(self, qapp):
        Theme.apply_to_app(qapp, "dark")
        Theme.apply_to_app(qapp, "light")

        expected = QColor(Theme.get_colors("light")["table-selected-bg"])
        assert qapp.palette().color(QPalette.ColorRole.Highlight) == expected

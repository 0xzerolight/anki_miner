"""Regression tests for the dark-mode menu-bar readability fix.

The top strip is a native ``QMenuBar`` holding the File/Edit menus plus a corner
widget with the Report a Bug / Star / Join Discord buttons. common.qss carried no
``QMenuBar`` rule and no rule for the corner button object names, so once an
app-wide stylesheet was applied Qt left the menu-bar background at the platform
default (white on Windows) while ``QWidget { color: ... }`` painted the text
near-white -> white-on-white, unreadable in dark themes.

These tests pin the menu-bar theming so a future common.qss refactor that drops
it resurfaces in CI instead of in a user screenshot.
"""

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.resources.styles.theme import Theme


@pytest.fixture(autouse=True)
def _clear_app_stylesheet():
    """Theme.get_stylesheet is pure, but keep parity with sibling theme tests and
    clear any leaked app stylesheet in teardown."""
    yield
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyleSheet("")


class TestMenuBarStylesheet:
    def test_stylesheet_defines_qmenubar_rule(self):
        qss = Theme.get_stylesheet("dark")
        assert "QMenuBar {" in qss
        assert "QMenuBar::item" in qss

    def test_stylesheet_themes_corner_buttons(self):
        qss = Theme.get_stylesheet("dark")
        for object_name in (
            "report_issue_button",
            "github_star_button",
            "discord_button",
        ):
            assert f"QToolButton#{object_name}" in qss

    def test_stylesheet_themes_corner_container(self):
        qss = Theme.get_stylesheet("dark")
        assert "QWidget#menu_corner_widget" in qss

    def test_menu_bar_colors_are_resolved_not_placeholders(self):
        """The compiled QSS must substitute the ${color-*} tokens in the menu-bar
        block; an unresolved ${...} would mean the strip is not actually themed."""
        qss = Theme.get_stylesheet("dark")
        start = qss.index("QMenuBar {")
        end = qss.index("/* --- Combo Box Dropdown --- */", start)
        menu_bar_block = qss[start:end]
        assert "${" not in menu_bar_block
        # The strip background resolves to the dark theme's window color.
        assert "#0F172A" in menu_bar_block

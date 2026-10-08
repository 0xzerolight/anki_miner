"""Themes render exactly as their author wrote them (decision D43-A).

The owner's ruling: the app may not derive, substitute or reject a single
colour, and it may not grow ``REQUIRED_COLOR_KEYS``. These tests pin:

1. ``Theme.get_colors`` hands out the JSON on disk unchanged for every shipped
   theme, and a live preview leaves it that way.
2. Applying a theme writes the author's colours into the Qt palette verbatim,
   even for a theme that is hard to read.
3. All 29 shipped themes still load, compile and render.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.panels.ui_settings_panel import UISettingsPanel

# --------------------------------------------------------------------------
# Shipped themes: all 29 still load, compile, render, and stay author-exact.
# --------------------------------------------------------------------------


def _shipped_theme_files() -> list[Path]:
    files = sorted((get_resource_dir() / "styles" / "themes").glob("*.json"))
    assert len(files) == 29, f"expected 29 shipped themes, found {len(files)}"
    return files


SHIPPED_KEYS = [p.stem for p in _shipped_theme_files()]


@pytest.fixture
def shipped_themes() -> Iterator[None]:
    """Force the real shipped theme directory (conftest resets Theme per test)."""
    Theme.initialize(active="light", shipped_dir=None)
    yield


class TestShippedThemesStayAuthorExact:
    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_get_colors_matches_the_file_on_disk(self, shipped_themes, key: str):
        on_disk = json.loads((get_resource_dir() / "styles" / "themes" / f"{key}.json").read_text(encoding="utf-8"))

        assert Theme.get_colors(key) == on_disk["colors"]


class TestShippedThemesRender:
    """Compile + polish + paint every shipped theme."""

    @pytest.fixture
    def sample_tree(self, qtbot) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        for widget in (QPushButton("Mine"), QLabel("Ready"), QLineEdit(), QComboBox(), QTextEdit(), QProgressBar()):
            layout.addWidget(widget)
        host.findChild(QLineEdit).setPlaceholderText("Search")
        qtbot.addWidget(host)
        return host

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_stylesheet_has_no_unresolved_variables(self, shipped_themes, key: str):
        # The file header documents the ${…} syntax in a comment; strip comments
        # before looking for placeholders the substitution failed to resolve.
        rules = re.sub(r"/\*.*?\*/", "", Theme.get_stylesheet(key), flags=re.DOTALL)

        assert "${" not in rules

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_theme_polishes_and_paints_without_qt_complaints(self, shipped_themes, key: str, sample_tree: QWidget):
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        messages: list[str] = []

        def handler(mode: QtMsgType, context, message: str) -> None:
            if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
                messages.append(message)

        previous = qInstallMessageHandler(handler)
        try:
            app.setStyleSheet(Theme.get_stylesheet(key))
            sample_tree.ensurePolished()
            for child in sample_tree.findChildren(QWidget):
                child.ensurePolished()
            sample_tree.resize(320, 260)
            image = sample_tree.grab().toImage()
        finally:
            qInstallMessageHandler(previous)
            app.setStyleSheet("")

        assert not image.isNull()
        assert messages == []


# --------------------------------------------------------------------------
# The live preview
# --------------------------------------------------------------------------

CLEAR_THEME = {
    "background": "#ffffff",
    "surface": "#d0d0d0",
    "text-muted": "#595959",
    "primary": "#0000cc",
    "text-on-primary": "#ffffff",
}
# Hard to read on purpose: D43-A says it still applies exactly as written.
MURKY_THEME = {
    "background": "#202020",
    "surface": "#212121",
    "input-bg": "#232323",
    "text": "#242424",
    "text-disabled": "#252525",
    "input-disabled-bg": "#262626",
    "disabled": "#272727",
    "text-muted": "#303030",
    "primary": "#808080",
    "text-on-primary": "#999999",
}


def _theme_file(path: Path, name: str, colors: dict[str, str]) -> None:
    data = {"name": name, "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000") | colors}
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def themes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "themes"
    d.mkdir()
    _theme_file(d / "clear.json", "Clear", CLEAR_THEME)
    _theme_file(d / "murky.json", "Murky", MURKY_THEME)
    return d


@pytest.fixture
def panel(qapp, qtbot, themes_dir: Path) -> UISettingsPanel:
    Theme.initialize(active="clear", favorites=("clear",), shipped_dir=themes_dir)
    p = UISettingsPanel(themes_dir)
    qtbot.addWidget(p)
    return p


def _card(panel: UISettingsPanel, key: str):
    card = panel.gallery.card(key)
    assert card is not None, f"no card for {key!r}"
    return card


class TestPreviewDoesNotRepaintColours:
    """Applying a theme writes the author's colours into the palette verbatim."""

    def test_preview_leaves_the_theme_colours_untouched(self, panel: UISettingsPanel, themes_dir: Path):
        on_disk = json.loads((themes_dir / "murky.json").read_text(encoding="utf-8"))["colors"]

        _card(panel, "murky").click()

        assert Theme.get_colors("murky") == on_disk

    def test_palette_matches_the_authored_values(self, panel: UISettingsPanel, qapp):
        _card(panel, "murky").click()

        palette = qapp.palette()
        assert palette.color(QPalette.ColorRole.Window).name() == MURKY_THEME["background"]
        assert palette.color(QPalette.ColorRole.Base).name() == MURKY_THEME["input-bg"]
        assert palette.color(QPalette.ColorRole.Button).name() == MURKY_THEME["surface"]
        assert palette.color(QPalette.ColorRole.Accent).name() == MURKY_THEME["primary"]
        qapp.setStyleSheet("")

    def test_the_disabled_group_uses_the_theme_s_own_disabled_tokens(self, panel: UISettingsPanel, qapp):
        """Not a dimmed copy of the enabled colours — the author wrote these."""
        _card(panel, "murky").click()

        palette = qapp.palette()
        disabled = QPalette.ColorGroup.Disabled
        assert palette.color(disabled, QPalette.ColorRole.Base).name() == MURKY_THEME["input-disabled-bg"]
        assert palette.color(disabled, QPalette.ColorRole.Button).name() == MURKY_THEME["disabled"]
        assert palette.color(disabled, QPalette.ColorRole.Text).name() == MURKY_THEME["text-disabled"]
        qapp.setStyleSheet("")

    def test_inactive_reads_the_same_as_active(self, panel: UISettingsPanel, qapp):
        """An unfocused window is the same window; Qt's default dimming is not."""
        _card(panel, "murky").click()

        palette = qapp.palette()
        for role in (
            QPalette.ColorRole.Window,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Highlight,
        ):
            assert palette.color(QPalette.ColorGroup.Inactive, role) == palette.color(QPalette.ColorGroup.Active, role)
        qapp.setStyleSheet("")

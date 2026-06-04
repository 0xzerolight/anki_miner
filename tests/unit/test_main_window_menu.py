"""Tests for :class:`MainWindow` menu bar wiring.

Covers the Help menu's Report-a-Bug action label, the GitHub star corner widget
on the menu bar, and the URLs each opens. Like ``test_main_window_close``, this
file builds a real ``MainWindow`` with heavy startup side effects patched out.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

from anki_miner.config import AnkiMinerConfig

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)


@pytest.fixture
def main_window(monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.deleteLater()


def _find_action(menu, text: str):
    """Return the action whose text equals ``text``, or None."""
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def _help_menu(window):
    menu_bar = window.menuBar()
    assert menu_bar is not None
    for action in menu_bar.actions():
        # Menu titles use "&Help"; strip the ampersand for comparison.
        if action.text().replace("&", "") == "Help":
            menu = action.menu()
            assert menu is not None
            return menu
    raise AssertionError("Help menu not found on menu bar")


def _corner_container(window):
    """Return the QWidget pinned to the menu bar's top-right corner."""
    menu_bar = window.menuBar()
    assert menu_bar is not None
    corner = menu_bar.cornerWidget(Qt.Corner.TopRightCorner)
    assert isinstance(corner, QWidget), "Top-right corner should hold a container QWidget"
    return corner


def test_report_removed_from_help_menu(main_window):
    """The Report item no longer lives on the Help menu."""
    help_menu = _help_menu(main_window)
    assert _find_action(help_menu, "Report a Bug / Suggest a Feature") is None
    assert _find_action(help_menu, "Report an Issue") is None
    # Help retains About + Check for Updates.
    assert _find_action(help_menu, "About Anki Miner") is not None
    assert _find_action(help_menu, "Check for Updates") is not None


def test_corner_has_report_and_star_buttons(main_window):
    """The corner container holds a Report button and the Star button."""
    container = _corner_container(main_window)
    report = container.findChild(QToolButton, "report_issue_button")
    star = container.findChild(QToolButton, "github_star_button")
    assert report is not None, "Report button missing from corner container"
    assert star is not None, "Star button missing from corner container"
    assert report.text() == "Report a Bug / Suggest a Feature"
    assert "Star - help the project" in star.text()
    assert report.autoRaise() is True
    assert star.autoRaise() is True


def test_report_button_opens_issues_url(main_window, monkeypatch):
    """Clicking the Report button opens the /issues page via QDesktopServices."""
    captured: list[QUrl] = []
    from PyQt6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: captured.append(url) or True)

    report = _corner_container(main_window).findChild(QToolButton, "report_issue_button")
    assert report is not None
    report.click()

    assert len(captured) == 1
    assert captured[0].toString() == "https://github.com/0xzerolight/anki_miner/issues"


def test_star_button_in_corner_container(main_window):
    """A QToolButton labelled 'Star - help the project' sits in the corner container."""
    star = _corner_container(main_window).findChild(QToolButton, "github_star_button")
    assert star is not None
    assert "Star - help the project" in star.text()
    assert star.toolTip() == "Star the project on GitHub"
    assert star.autoRaise() is True


def test_star_button_opens_repo_url(main_window, monkeypatch):
    """Clicking the star button opens the repo root via QDesktopServices."""
    captured: list[QUrl] = []
    from PyQt6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: captured.append(url) or True)

    star = _corner_container(main_window).findChild(QToolButton, "github_star_button")
    assert star is not None
    star.click()

    assert len(captured) == 1
    assert captured[0].toString() == "https://github.com/0xzerolight/anki_miner"


def test_about_html_credits_bundled_ffmpeg():
    """The About dialog body credits the bundled GPLv3 FFmpeg build with a link."""
    from anki_miner.gui.main_window import _build_about_html

    html = _build_about_html("9.9.9")
    assert "9.9.9" in html
    assert "FFmpeg" in html
    assert "GPLv3" in html
    assert "https://ffmpeg.org" in html
    assert "licenses/ffmpeg/" in html

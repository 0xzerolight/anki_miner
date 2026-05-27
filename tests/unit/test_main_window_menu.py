"""Tests for :class:`MainWindow` menu bar wiring.

Covers the Help menu's Report-a-Bug action label, the GitHub star corner widget
on the menu bar, and the URLs each opens. Like ``test_main_window_close``, this
file builds a real ``MainWindow`` with heavy startup side effects patched out.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QApplication, QToolButton

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


def test_report_action_label_mentions_bug_and_feature(main_window):
    """The Help menu entry advertises both bug reports and feature requests."""
    help_menu = _help_menu(main_window)
    action = _find_action(help_menu, "Report a Bug / Suggest a Feature")
    assert action is not None, "Expected renamed Report action on Help menu"
    # Old label must be gone.
    assert _find_action(help_menu, "Report an Issue") is None


def test_report_action_opens_issues_url(main_window, monkeypatch):
    """Triggering the Report action opens the /issues page via QDesktopServices."""
    captured: list[QUrl] = []
    from PyQt6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: captured.append(url) or True)

    action = _find_action(_help_menu(main_window), "Report a Bug / Suggest a Feature")
    assert action is not None
    action.trigger()

    assert len(captured) == 1
    assert captured[0].toString() == "https://github.com/0xzerolight/anki_miner/issues"


def test_menu_bar_has_github_star_corner_widget(main_window):
    """A QToolButton labelled 'Star - help the project' sits in the top-right corner."""
    menu_bar = main_window.menuBar()
    assert menu_bar is not None

    corner = menu_bar.cornerWidget(Qt.Corner.TopRightCorner)
    assert isinstance(corner, QToolButton), "Top-right corner should hold a QToolButton"
    assert "Star - help the project" in corner.text()
    assert corner.toolTip() == "Star the project on GitHub"
    assert corner.autoRaise() is True


def test_star_button_opens_repo_url(main_window, monkeypatch):
    """Clicking the star button opens the repo root via QDesktopServices."""
    captured: list[QUrl] = []
    from PyQt6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: captured.append(url) or True)

    menu_bar = main_window.menuBar()
    assert menu_bar is not None
    corner = menu_bar.cornerWidget(Qt.Corner.TopRightCorner)
    assert isinstance(corner, QToolButton)
    corner.click()

    assert len(captured) == 1
    assert captured[0].toString() == "https://github.com/0xzerolight/anki_miner"

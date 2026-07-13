"""Tests for :class:`MainWindow` menu bar wiring.

Covers the Help menu's Report-a-Bug action label, the GitHub star corner widget
on the menu bar, and the URLs each opens. Like ``test_main_window_close``, this
file builds a real ``MainWindow`` with heavy startup side effects patched out.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtWidgets import QToolButton, QWidget


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
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
    discord = container.findChild(QToolButton, "discord_button")
    assert report is not None, "Report button missing from corner container"
    assert star is not None, "Star button missing from corner container"
    assert discord is not None, "Discord button missing from corner container"
    assert report.text() == "Report a Bug / Suggest a Feature"
    assert "Star - help the project" in star.text()
    assert discord.text() == "Join Discord"
    assert report.autoRaise() is True
    assert star.autoRaise() is True
    assert discord.autoRaise() is True
    assert discord.toolTip() == "Join the community on Discord"


def test_discord_button_has_brand_icon(main_window):
    """The Discord button shows the blurple brand mark beside its unchanged label."""
    discord = _corner_container(main_window).findChild(QToolButton, "discord_button")
    assert discord is not None
    assert discord.text() == "Join Discord"  # label intentionally unchanged
    assert discord.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    icon = discord.icon()
    assert not icon.isNull()
    # The mark rasterizes at 16px and its core fill is Discord brand blurple #5865F2.
    pixmap = icon.pixmap(QSize(16, 16))
    assert not pixmap.isNull()
    center = pixmap.toImage().pixelColor(8, 8)
    assert (center.red(), center.green(), center.blue(), center.alpha()) == (0x58, 0x65, 0xF2, 255)


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


def _tools_menu(window):
    menu_bar = window.menuBar()
    assert menu_bar is not None
    for action in menu_bar.actions():
        if action.text().replace("&", "") == "Tools":
            menu = action.menu()
            assert menu is not None
            return menu
    raise AssertionError("Tools menu not found on menu bar")


def test_find_a_feature_action_present(main_window):
    """Tools menu exposes the Find a Feature browser entry."""
    assert _find_action(_tools_menu(main_window), "Find a Feature...") is not None


def test_find_a_feature_opens_browser(main_window, monkeypatch):
    """Triggering the action runs the capability browser, parented to the window."""
    from anki_miner.gui.widgets.dialogs import capability_browser

    calls: list[tuple] = []
    monkeypatch.setattr(capability_browser, "run_capability_browser", lambda parent, mw: calls.append((parent, mw)))
    action = _find_action(_tools_menu(main_window), "Find a Feature...")
    assert action is not None
    action.trigger()
    assert calls == [(main_window, main_window)]


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


def test_discord_button_opens_invite_url(main_window, monkeypatch):
    """Clicking the Discord button opens the community invite via QDesktopServices."""
    captured: list[QUrl] = []
    from PyQt6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: captured.append(url) or True)

    discord = _corner_container(main_window).findChild(QToolButton, "discord_button")
    assert discord is not None
    discord.click()

    assert len(captured) == 1
    assert captured[0].toString() == "https://discord.com/invite/aDtQyZzUVP"


def test_about_dialog_builds_and_shows_version(qtbot):
    """AboutDialog constructs headless and renders the version."""
    from PyQt6.QtWidgets import QLabel

    from anki_miner.gui.widgets.dialogs.about_dialog import AboutDialog

    dialog = AboutDialog("9.9.9")
    qtbot.addWidget(dialog)
    try:
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("9.9.9" in t for t in texts)
    finally:
        dialog.deleteLater()

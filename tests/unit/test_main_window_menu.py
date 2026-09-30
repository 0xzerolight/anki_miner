"""Tests for :class:`MainWindow` menu bar wiring.

Covers the Help menu's Report-a-Bug action label, the GitHub star corner widget
on the menu bar, and the URLs each opens. Like ``test_main_window_close``, this
file builds a real ``MainWindow`` with heavy startup side effects patched out.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QImage
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
    assert _find_action(help_menu, "Send feedback") is None
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
    assert report.text() == "Send feedback"
    assert star.text() == "Star on GitHub"
    assert discord.text() == "Join Discord"
    assert report.autoRaise() is True
    assert star.autoRaise() is True
    assert discord.autoRaise() is True
    # The label already says it; a tooltip repeating it is noise, not help.
    assert discord.toolTip() == ""


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
    """A QToolButton labelled 'Star on GitHub' sits in the corner container."""
    star = _corner_container(main_window).findChild(QToolButton, "github_star_button")
    assert star is not None
    assert star.text() == "Star on GitHub"
    # The tooltip restated the label verbatim, so it is gone.
    assert star.toolTip() == ""
    assert star.autoRaise() is True
    # A gold star mark sits beside the label, laid out like the Discord mark.
    assert star.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    pixmap = star.icon().pixmap(QSize(16, 16))
    assert not pixmap.isNull()
    center = pixmap.toImage().pixelColor(8, 8)
    assert (center.red(), center.green(), center.blue(), center.alpha()) == (0xE3, 0xB3, 0x41, 255)


def _tools_menu(window):
    menu_bar = window.menuBar()
    assert menu_bar is not None
    for action in menu_bar.actions():
        if action.text().replace("&", "") == "Tools":
            menu = action.menu()
            assert menu is not None
            return menu
    raise AssertionError("Tools menu not found on menu bar")


def test_usage_guide_sits_after_help(main_window):
    """The Usage Guide is its own top-level menu-bar item, to the right of Help."""
    menu_bar = main_window.menuBar()
    assert menu_bar is not None
    texts = [a.text() for a in menu_bar.actions()]
    assert texts.index("&Tools") < texts.index("&Help") < texts.index("Usage Guide")


def test_usage_guide_is_a_plain_action_on_non_native_bars(main_window):
    """Off the native menu bar it is a one-click button, not a dropdown."""
    assert main_window.usage_guide_action.menu() is None


def test_tools_menu_no_longer_lists_find_a_feature(main_window):
    labels = [a.text() for a in _tools_menu(main_window).actions()]
    assert "Find a Feature..." not in labels
    assert not any("Usage Guide" in label for label in labels)


def test_tools_menu_no_longer_lists_restyle(main_window):
    """Restyle moved to a button on Utilities -> Card Backfill (Task 14)."""
    labels = [a.text() for a in _tools_menu(main_window).actions()]
    assert not any("Restyle" in label for label in labels)


def test_usage_guide_action_opens_browser(main_window, monkeypatch):
    """Triggering the action runs the capability browser, parented to the window.

    The active mining language's capabilities ride along, so the guide lists
    only the features that language can use.
    """
    from anki_miner.gui.widgets.dialogs import capability_browser
    from anki_miner.languages.registry import config_language, get_profile

    calls: list[tuple] = []
    monkeypatch.setattr(
        capability_browser,
        "run_capability_browser",
        lambda parent, mw, capabilities: calls.append((parent, mw, capabilities)),
    )
    main_window.usage_guide_action.trigger()
    expected = get_profile(config_language(main_window.config)).capabilities
    assert calls == [(main_window, main_window, expected)]


def test_native_menu_bar_gets_one_action_menu(qtbot, patch_heavy_init, test_config, monkeypatch):
    """On a native menu bar (macOS, Linux global menu) a menu-less top-level
    QAction is silently dropped, so the Usage Guide becomes a one-action menu."""
    from PyQt6.QtWidgets import QMenuBar

    from anki_miner.gui.utils.key_bindings import default_sequence

    monkeypatch.setattr(QMenuBar, "isNativeMenuBar", lambda self: True)
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    try:
        menu_bar = window.menuBar()
        assert menu_bar is not None
        guide_actions = [a for a in menu_bar.actions() if a.text() == "Usage Guide"]
        assert len(guide_actions) == 1
        menu = guide_actions[0].menu()
        assert menu is not None
        inner = menu.actions()
        assert [a.text() for a in inner] == ["Open Usage Guide..."]
        assert inner[0].shortcut() == default_sequence("app.usage_guide")
        assert window.usage_guide_action is inner[0]
    finally:
        window.deleteLater()


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


@pytest.fixture
def themed_app():
    """Apply the real theme stylesheet; its corner-button padding exposed the lopsided layout."""
    from PyQt6.QtWidgets import QApplication

    from anki_miner.gui.resources.styles.theme import Theme

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    app.setStyleSheet(Theme.get_stylesheet("light"))
    yield
    app.setStyleSheet("")


def _ink_columns(button: QToolButton) -> list[int]:
    """Columns of ``button``'s rendering that carry mark or label ink."""
    # The window is never shown, so give the corner container its size hint and lay
    # it out, as QMenuBar does; otherwise it keeps the 100px default and squeezes
    # every button.
    corner = button.parentWidget()
    assert corner is not None
    layout = corner.layout()
    assert layout is not None
    corner.resize(corner.sizeHint())
    layout.activate()
    assert button.size() == button.sizeHint()
    image = QImage(button.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    button.render(image)
    background = image.pixelColor(0, image.height() // 2)

    def is_ink(x: int, y: int) -> bool:
        pixel = image.pixelColor(x, y)
        return (
            abs(pixel.red() - background.red())
            + abs(pixel.green() - background.green())
            + abs(pixel.blue() - background.blue())
            + abs(pixel.alpha() - background.alpha())
            > 120
        )

    return [x for x in range(image.width()) if any(is_ink(x, y) for y in range(image.height()))]


@pytest.mark.parametrize("object_name", ["github_star_button", "discord_button"])
def test_corner_mark_and_label_are_centred_with_a_gap(main_window, themed_app, object_name):
    """The mark and label sit centred, with clear space between them.

    Qt's TextBesideIcon layout packs [2px | mark | 2px | label] against the left
    edge and the QSS padding only widened the button, so the mark touched the
    label and ~27px of slack sat right of it against ~3px on the left.
    """
    button = _corner_container(main_window).findChild(QToolButton, object_name)
    assert button is not None
    ink = _ink_columns(button)
    assert ink, "nothing rendered"

    left_pad = ink[0]
    right_pad = button.width() - 1 - ink[-1]
    assert abs(left_pad - right_pad) <= 3, (left_pad, right_pad)

    mark_width = button.iconSize().width()
    mark_end = max(x for x in ink if x < ink[0] + mark_width)
    label_start = min(x for x in ink if x >= ink[0] + mark_width)
    assert label_start - mark_end - 1 >= 5, (mark_end, label_start)


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


def test_open_log_folder_uses_effective_sink_path(main_window, monkeypatch, tmp_path):
    from anki_miner.gui import main_window as main_window_module

    effective_path = tmp_path / "fallback" / "AnkiMiner-early-crash.log"
    opened = []
    monkeypatch.setattr(
        main_window_module,
        "get_effective_log_path",
        lambda configured_path: effective_path,
        raising=False,
    )
    monkeypatch.setattr(main_window_module, "open_log_folder", lambda log_path: opened.append(log_path), raising=False)

    action = _find_action(_help_menu(main_window), "Open Log Folder")
    assert action is not None
    action.trigger()

    assert opened == [effective_path]


def test_export_diagnostics_immediately_follows_open_log_folder(main_window):
    """The two log-report actions stay adjacent in the Help menu."""
    actions = _help_menu(main_window).actions()
    open_log_index = next(index for index, action in enumerate(actions) if action.text() == "Open Log Folder")

    export_action = actions[open_log_index + 1]

    assert not export_action.isSeparator()
    assert export_action.text() == "Export Diagnostics…"
    assert export_action.toolTip() == "Save a zip with logs and system details for a bug report"
    # A QMenu hides action tooltips unless asked, so the surviving one is shown;
    # Open Log Folder's tooltip only restated its label and is gone.
    assert _help_menu(main_window).toolTipsVisible() is True
    assert actions[open_log_index].toolTip() == "Open Log Folder"


def test_about_dialog_builds_and_shows_version(qtbot):
    """AboutDialog constructs headless and renders the version."""
    from PyQt6.QtWidgets import QLabel

    from anki_miner.gui.utils.key_bindings import about_rows, resolve_bindings
    from anki_miner.gui.widgets.dialogs.about_dialog import AboutDialog

    dialog = AboutDialog("9.9.9", about_rows(resolve_bindings({})))
    qtbot.addWidget(dialog)
    try:
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("9.9.9" in t for t in texts)
    finally:
        dialog.deleteLater()


def test_system_health_is_the_first_tools_item(main_window):
    """E06: System Health was reachable only through the status-bar badges."""
    labels = [a.text() for a in _tools_menu(main_window).actions() if not a.isSeparator()]

    assert labels[0] == "System Health…"


def test_the_tools_item_opens_system_health(main_window):
    action = _find_action(_tools_menu(main_window), "System Health…")

    action.trigger()
    try:
        assert main_window._system_health_window is not None
        assert main_window._system_health_window.isVisible()
    finally:
        main_window._system_health_window.close()

"""The sliding tab underline.

Navigation is the one place where motion is most tempting and most dangerous:
the underline is decoration, the page switch is the actual command. So the two
are deliberately not coupled -- the bar listens to ``QTabWidget.currentChanged``,
which Qt emits *after* the page has already changed, and only then starts
moving. Every assertion here exists to keep that ordering, and to keep a second
rapid click from stranding the underline between two tabs.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPropertyAnimation, QRectF
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel, QTabBar, QTabWidget, QWidget

from anki_miner.gui.resources.styles import MOTION
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils import motion
from anki_miner.gui.widgets.base.animated_tab_bar import AnimatedTabBar, install_animated_tab_bar

_ACCENT = "#ff8800"


@pytest.fixture(autouse=True)
def _known_stylesheet(qapp):
    """Paint against a known sheet, not whatever another module left installed."""
    previous = qapp.styleSheet()
    previous_palette = QPalette(qapp.palette())
    qapp.setStyleSheet("")
    yield
    qapp.setStyleSheet(previous)
    qapp.setPalette(previous_palette)


@pytest.fixture
def tabs(qtbot):
    """A three-page QTabWidget wearing the animated bar, shown and laid out."""
    widget = QTabWidget()
    install_animated_tab_bar(widget)
    for name in ("Single", "Batch", "YouTube"):
        page = QLabel(name)
        page.setObjectName(f"page-{name}")
        widget.addTab(page, name)
    qtbot.addWidget(widget)
    widget.resize(480, 260)
    widget.show()
    qtbot.waitUntil(lambda: widget.tabBar().tabRect(2).width() > 0)
    return widget


def _underline_of(bar: AnimatedTabBar, index: int) -> QRectF:
    rect = bar.tabRect(index)
    return QRectF(rect.x(), rect.y() + rect.height() - 3, rect.width(), 3)


class TestInstallation:
    def test_the_tab_widget_keeps_working(self, tabs):
        assert isinstance(tabs.tabBar(), AnimatedTabBar)
        assert tabs.count() == 3

        tabs.setCurrentIndex(2)

        assert tabs.currentWidget().objectName() == "page-YouTube"

    def test_the_underline_starts_under_the_selected_tab(self, tabs):
        assert tabs.tabBar().property("underlineRect") == _underline_of(tabs.tabBar(), 0)


class TestSliding:
    def test_the_page_has_already_switched_before_the_underline_moves(self, tabs, monkeypatch):
        """Motion may never be on the path between the click and the page."""
        seen: list[tuple[str, bool]] = []
        real = motion.animate

        def spy(*args, **kwargs):
            page = tabs.currentWidget()
            seen.append((page.objectName(), page.isVisible()))
            return real(*args, **kwargs)

        monkeypatch.setattr(motion, "animate", spy)

        tabs.setCurrentIndex(2)

        assert seen == [("page-YouTube", True)]

    def test_the_underline_lands_on_the_selected_tab(self, tabs):
        bar = tabs.tabBar()

        with motion.instant():
            tabs.setCurrentIndex(1)

        assert bar.property("underlineRect") == _underline_of(bar, 1)

    def test_it_travels_at_the_navigation_duration(self, tabs):
        tabs.setCurrentIndex(1)

        assert tabs.tabBar().findChildren(QPropertyAnimation)[0].duration() == MOTION.navigation

    def test_a_second_click_retargets_the_same_animation(self, tabs):
        """Queueing a second animation would tear; restarting would jump back."""
        bar = tabs.tabBar()

        tabs.setCurrentIndex(2)
        first = bar.findChildren(QPropertyAnimation)
        mid_flight = bar.property("underlineRect")
        tabs.setCurrentIndex(1)
        second = bar.findChildren(QPropertyAnimation)

        assert len(second) == 1
        assert second == first
        assert second[0].startValue() == mid_flight
        assert second[0].endValue() == _underline_of(bar, 1)

    def test_the_animation_finishes_on_the_target(self, tabs, qtbot):
        bar = tabs.tabBar()

        tabs.setCurrentIndex(2)

        qtbot.waitUntil(lambda: bar.property("underlineRect") == _underline_of(bar, 2))


class TestSnapping:
    def test_a_resize_moves_the_underline_immediately(self, tabs, qtbot):
        bar = tabs.tabBar()
        tabs.setCurrentIndex(2)
        bar.snap_underline()

        tabs.resize(900, 260)
        qtbot.waitUntil(lambda: bar.tabRect(2).width() > 0)

        assert bar.property("underlineRect") == _underline_of(bar, 2)
        assert not motion.active_animations(bar)

    def test_a_relabelled_tab_moves_the_underline_immediately(self, tabs):
        """Translated labels change every tab's width, mid-slide or not."""
        bar = tabs.tabBar()

        bar.setTabText(0, "Single episode, spelled out at length")

        assert bar.property("underlineRect") == _underline_of(bar, 0)
        assert not motion.active_animations(bar)

    def test_an_added_tab_moves_the_underline_immediately(self, tabs):
        bar = tabs.tabBar()
        tabs.insertTab(0, QWidget(), "Newest")

        assert bar.property("underlineRect") == _underline_of(bar, bar.currentIndex())
        assert not motion.active_animations(bar)

    def test_an_empty_bar_draws_no_underline(self, tabs, qtbot):
        bar = tabs.tabBar()

        while tabs.count():
            tabs.removeTab(0)

        assert bar.property("underlineRect").isEmpty()
        bar.grab()  # must not raise: painting an empty underline is a no-op


class TestPainting:
    def test_the_underline_is_drawn_in_the_accent_colour(self, tabs, qapp):
        qapp.setStyleSheet(f"AnimatedTabBar {{ qproperty-accentColour: {_ACCENT}; }}")
        bar = tabs.tabBar()

        underline = _underline_of(bar, 0)
        painted = bar.grab().toImage().pixelColor(int(underline.center().x()), int(underline.center().y()))

        assert painted == QColor(_ACCENT)

    def test_the_static_selected_border_is_off_for_this_class(self, tabs, qtbot, qapp):
        """Two indicators is one too many, and the static one cannot slide.

        The plain QTabBar keeps it: the app has other tab bars this workstream
        does not own.
        """
        qapp.setStyleSheet(Theme.get_stylesheet("light"))
        accent = QColor(Theme.get_colors("light")["primary"])
        plain = QTabWidget()
        for name in ("Single", "Batch"):
            plain.addTab(QWidget(), name)
        qtbot.addWidget(plain)
        plain.resize(480, 260)
        plain.show()
        qtbot.waitUntil(lambda: plain.tabBar().tabRect(0).width() > 0)

        def bottom_of_selected(bar: QTabBar) -> QColor:
            rect = bar.tabRect(bar.currentIndex())
            return bar.grab().toImage().pixelColor(rect.center().x(), rect.bottom() - 1)

        assert bottom_of_selected(plain.tabBar()) == accent, "the plain-bar indicator was removed globally"

        animated = tabs.tabBar()
        with motion.instant():
            animated.setProperty("underlineRect", QRectF())

        assert bottom_of_selected(animated) != accent


class TestInstalledEverywhere:
    def test_the_main_tab_bar_slides(self, qtbot, patch_heavy_init, test_config):
        patch_heavy_init(test_config)
        from anki_miner.gui.main_window import MainWindow

        window = MainWindow(test_config)
        qtbot.addWidget(window)

        assert isinstance(window.tabs.tabBar(), AnimatedTabBar)

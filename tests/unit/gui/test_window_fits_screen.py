"""The main window never demands more room than the screen it is on.

The 1024x768 ``WINDOW_MIN_*`` contract is written in logical pixels, and a
Windows 1080p laptop at the 150% scaling Windows recommends is a 1280x720
logical desktop with a ~672px work area. A minimum taller than the work area is
enforced by Windows on every sizing operation, so restoring from maximised lands
back on a screen-filling rect and the borders stop dragging -- reported as "can
maximize the app, can't undo it later, can't change the size of it as well".

The small-screen cases live on the pure helper: no CI display is 1280x672, and
the offscreen platform gives a fixed screen this test cannot resize. The live
window covers the invariant (minimum inside the screen) and the two behaviours
the fix adds (shrink an oversized window, never touch a maximised one).
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.constants import WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH
from anki_miner.gui.utils.qt_helpers import fit_window_minimum


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _available():
    screen = QApplication.primaryScreen()
    assert screen is not None
    return screen.availableGeometry()


class TestTheMinimumIsCappedAtTheScreen:
    def test_a_1080p_laptop_at_150_percent_loses_the_height_floor_only(self):
        """1920x1080 at 150% -> 1280x720 logical, ~672px of work area."""
        fitted = fit_window_minimum(QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT), QSize(1280, 672))

        assert fitted == QSize(1024, 672)

    def test_a_1366x768_laptop_loses_the_height_floor_only(self):
        fitted = fit_window_minimum(QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT), QSize(1366, 728))

        assert fitted == QSize(1024, 728)

    def test_both_axes_shrink_when_both_are_short(self):
        fitted = fit_window_minimum(QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT), QSize(800, 600))

        assert fitted == QSize(800, 600)

    def test_a_roomy_screen_keeps_the_full_contract(self):
        fitted = fit_window_minimum(QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT), QSize(2560, 1400))

        assert fitted == QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)


class TestTheLiveWindowFitsItsScreen:
    def test_the_minimum_never_exceeds_the_available_area(self, main_window):
        available = _available()

        assert main_window.minimumWidth() <= available.width()
        assert main_window.minimumHeight() <= available.height()

    def test_an_oversized_window_is_shrunk_back_onto_the_screen(self, main_window, qtbot):
        """The restore from maximised hands back the oversized pre-maximise rect."""
        available = _available()
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window.resize(available.width() + 400, available.height() + 400)
        QApplication.processEvents()
        assert not available.contains(main_window.frameGeometry())  # non-vacuity

        main_window._apply_screen_fit()
        QApplication.processEvents()

        assert main_window.width() <= available.width()
        assert main_window.height() <= available.height()
        assert available.contains(main_window.frameGeometry())

    def test_a_window_already_inside_the_screen_is_left_alone(self, main_window, qtbot):
        """Idempotent: a fitted window is not nudged again on every screen event.

        Stated as a second pass rather than a first because the offscreen screen
        (800x800) is smaller than the 1280x800 default, so the window arrives
        oversized and the first pass legitimately moves it.
        """
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window._apply_screen_fit()
        QApplication.processEvents()
        before = main_window.geometry()

        main_window._apply_screen_fit()
        QApplication.processEvents()

        assert main_window.geometry() == before

    def test_a_maximised_window_is_never_resized(self, main_window, qtbot):
        """Fitting a maximised window would silently un-maximise it."""
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window.showMaximized()
        QApplication.processEvents()
        assert main_window.isMaximized()  # non-vacuity
        before = main_window.geometry()

        main_window._apply_screen_fit()
        QApplication.processEvents()

        assert main_window.isMaximized()
        assert main_window.geometry() == before


class TestLeavingTheMaximisedStateRefits:
    def test_showing_normal_after_maximised_lands_inside_the_screen(self, main_window, qtbot):
        available = _available()
        main_window.show()
        qtbot.waitExposed(main_window)
        # The pre-maximise rect a stuck user has: taller than the work area.
        main_window.resize(available.width() + 400, available.height() + 400)
        main_window.showMaximized()
        QApplication.processEvents()

        main_window.showNormal()
        # The refit is deferred one turn (Qt applies the restored geometry after
        # the state-change event), so the wait is load-bearing, not padding.
        qtbot.waitUntil(lambda: available.contains(main_window.frameGeometry()), timeout=2000)

        assert available.contains(main_window.frameGeometry())

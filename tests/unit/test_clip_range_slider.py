"""Tests for ClipRangeSlider — the curator's two-handle audio clip control."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt

from anki_miner.gui.widgets.audio_clip_editor import ClipRangeSlider


@pytest.fixture
def slider(qtbot) -> ClipRangeSlider:
    widget = ClipRangeSlider()
    qtbot.addWidget(widget)
    widget.resize(200, 24)
    widget.set_span(0, 100)
    widget.set_values(20, 60)
    return widget


class TestGeometry:
    def test_position_round_trips_through_ticks(self, slider):
        for ticks in (0, 25, 50, 100):
            assert slider._ticks_for(slider._pos_for(ticks)) == ticks

    def test_span_ends_sit_at_the_groove_ends(self, slider):
        groove = slider._groove_rect()
        assert slider._pos_for(0) == pytest.approx(groove.left())
        assert slider._pos_for(100) == pytest.approx(groove.right())

    def test_ticks_are_clamped_to_the_span(self, slider):
        assert slider._ticks_for(-500.0) == 0
        assert slider._ticks_for(5000.0) == 100

    def test_the_bar_can_hold_the_readout(self, slider):
        """Text drawn on a bar shorter than itself spills onto the page behind,
        where a light theme's background swallows it."""
        slider.set_text("2.6 s")
        assert slider._groove_rect().height() >= slider.fontMetrics().height()

    def test_degenerate_span_does_not_divide_by_zero(self, slider):
        slider.set_span(7, 7)
        assert slider._ticks_for(50.0) == 7
        assert slider._pos_for(7) == pytest.approx(slider._groove_rect().left())


class TestValues:
    def test_set_values_is_silent(self, slider):
        changes: list[tuple[int, int]] = []
        slider.values_changed.connect(lambda a, b: changes.append((a, b)))
        slider.set_values(10, 90)
        assert slider.values() == (10, 90)
        assert changes == []

    def test_set_values_clamps_to_the_span(self, slider):
        slider.set_values(-10, 500)
        assert slider.values() == (0, 100)

    def test_set_span_pulls_values_inside_it(self, slider):
        slider.set_span(30, 40)
        assert slider.values() == (30, 40)


class TestDragging:
    def _x_of(self, slider: ClipRangeSlider, ticks: int) -> QPoint:
        return QPoint(round(slider._pos_for(ticks)), slider.height() // 2)

    def test_dragging_the_near_handle_moves_it(self, qtbot, slider):
        changes: list[tuple[int, int]] = []
        slider.values_changed.connect(lambda a, b: changes.append((a, b)))
        qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=self._x_of(slider, 20))
        qtbot.mouseMove(slider, pos=self._x_of(slider, 35))
        qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=self._x_of(slider, 35))
        assert slider.values() == (35, 60)
        assert changes[-1] == (35, 60)

    def test_clicking_bare_groove_moves_the_nearest_handle(self, qtbot, slider):
        qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=self._x_of(slider, 95))
        assert slider.values() == (20, 95)

    def test_a_handle_cannot_cross_the_other(self, qtbot, slider):
        qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=self._x_of(slider, 20))
        qtbot.mouseMove(slider, pos=self._x_of(slider, 90))
        assert slider.values() == (60, 60)

    def test_disabled_slider_ignores_the_mouse(self, qtbot, slider):
        slider.setEnabled(False)
        qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=self._x_of(slider, 95))
        assert slider.values() == (20, 60)


class TestKeyboard:
    def test_arrow_nudges_the_active_handle(self, qtbot, slider):
        qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(round(slider._pos_for(20)), 12))
        qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(round(slider._pos_for(20)), 12))
        qtbot.keyClick(slider, Qt.Key.Key_Right)
        assert slider.values() == (21, 60)

    def test_arrow_emits(self, qtbot, slider):
        changes: list[tuple[int, int]] = []
        slider.values_changed.connect(lambda a, b: changes.append((a, b)))
        qtbot.keyClick(slider, Qt.Key.Key_Left)
        assert changes == [(19, 60)]


class TestReset:
    def test_double_click_requests_a_reset(self, qtbot, slider):
        resets: list[bool] = []
        slider.reset_requested.connect(lambda: resets.append(True))
        qtbot.mouseDClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(100, 12))
        assert resets == [True]

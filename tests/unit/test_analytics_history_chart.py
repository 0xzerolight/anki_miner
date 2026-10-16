"""ST-02: the history chart paints one bar per period, scaled to the largest."""

from __future__ import annotations

import pytest

from anki_miner.gui.widgets.analytics_tab import _BarChart


@pytest.fixture
def chart(qtbot) -> _BarChart:
    widget = _BarChart()
    qtbot.addWidget(widget)
    widget.resize(400, 160)
    return widget


def test_bars_scale_to_the_largest_value(chart):
    chart.set_bars([("2026-10-14", 5), ("2026-10-15", 10)])

    small, big = chart.bar_rects()

    assert big.height() == pytest.approx(2 * small.height())
    assert big.bottom() == pytest.approx(small.bottom())
    assert small.left() < big.left()


def test_no_bars_means_no_rects(chart):
    chart.set_bars([])

    assert chart.bar_rects() == []
    assert not chart.grab().isNull()


def test_zero_values_draw_flat_bars(chart):
    chart.set_bars([("a", 0), ("b", 0)])

    assert [rect.height() for rect in chart.bar_rects()] == [0.0, 0.0]


def test_bar_at_finds_the_bar_under_x(chart):
    chart.set_bars([("a", 1), ("b", 2)])
    rects = chart.bar_rects()

    assert chart.bar_at(rects[1].center().x()) == 1
    assert chart.bar_at(rects[0].center().x()) == 0
    assert chart.bar_at(-5.0) is None


def test_it_paints(chart):
    chart.set_bars([("2026-10-14", 3), ("2026-10-15", 7)])

    assert chart.bars() == [("2026-10-14", 3), ("2026-10-15", 7)]
    assert not chart.grab().isNull()

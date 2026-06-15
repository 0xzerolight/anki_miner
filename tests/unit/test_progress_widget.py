"""Tests for ProgressWidget (Issue: batch progress bar jumped to ~70%).

The QProgressBar's native scale is fixed at 0-100 because ``set_progress``
always converts ``current/total`` to a percentage. ``set_determinate`` must
NOT change the bar's maximum to ``maximum`` (the item count), otherwise
``setValue(percentage)`` against ``setMaximum(item_count)`` paints garbage
(e.g. value=8 on max=12 → 67% width on the first of 12 episodes).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.progress_widget import ProgressWidget


@pytest.fixture
def widget(qapp, qtbot):
    w = ProgressWidget()
    qtbot.addWidget(w)
    yield w
    w.deleteLater()


def test_set_determinate_keeps_progress_bar_max_at_100(widget):
    """Regression: set_determinate(12) must NOT set bar max to 12."""
    widget.set_determinate(12)
    assert widget.progress_bar.maximum() == 100


def test_set_determinate_stores_total_for_stats(widget):
    """Item count is still tracked separately for ETA/rate stats."""
    widget.set_determinate(12)
    assert widget.total == 12


def test_set_progress_first_step_renders_8_percent_on_12_items(widget):
    """Regression: 1/12 should render at 8%, not 67% (bug 1 repro)."""
    widget.set_determinate(12)
    widget.set_progress(1, 12, "Episode 1")
    assert widget.progress_bar.value() == 8
    assert widget.progress_bar.format() == "1/12"


def test_set_progress_full_renders_100_percent(widget):
    widget.set_determinate(12)
    widget.set_progress(12, 12, "Done")
    assert widget.progress_bar.value() == 100


def test_set_progress_advances_smoothly_episode_by_episode(widget):
    """Each step adds roughly 100/N percent; no jump on first step."""
    widget.set_determinate(5)
    expected = [0, 20, 40, 60, 80, 100]
    actual = []
    for i in range(6):
        widget.set_progress(i, 5, f"Step {i}")
        actual.append(widget.progress_bar.value())
    assert actual == expected


def test_progress_bar_text_hidden(widget):
    """No centered X/100 text painted on the bar, in any state."""
    assert widget.progress_bar.isTextVisible() is False
    widget.set_determinate(12)
    widget.set_progress(1, 12, "Episode 1")
    assert widget.progress_bar.isTextVisible() is False


def test_reset_restores_default_state(widget):
    widget.set_determinate(7)
    widget.set_progress(3, 7, "Mid")
    widget.reset()
    assert widget.progress_bar.maximum() == 100
    assert widget.progress_bar.value() == 0
    assert widget.progress_bar.format() == "%p%"
    assert widget.status_label.text() == "Ready"

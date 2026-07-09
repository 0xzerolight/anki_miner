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
    assert widget.status_label.text() == "Ready"


# ---------------------------------------------------------------------------
# set_percent / set_composed / show_completion (progress overhaul)
# ---------------------------------------------------------------------------


def test_set_percent_clamps_and_sets_value(widget):
    widget.set_percent(150)
    assert widget.progress_bar.value() == 100
    widget.set_percent(-5)
    assert widget.progress_bar.value() == 0
    widget.set_percent(42)
    assert widget.progress_bar.value() == 42


def test_set_percent_recovers_from_indeterminate(widget):
    widget.set_indeterminate()
    assert widget.progress_bar.maximum() == 0
    widget.set_percent(30)
    assert widget.progress_bar.maximum() == 100
    assert widget.progress_bar.value() == 30


def test_set_percent_keeps_eta_units_in_percent(widget):
    widget.set_determinate(12)  # would seed _total_items=12
    widget.set_percent(50)
    assert widget.total == 100


def test_set_percent_falsy_status_does_not_blank_label(widget):
    widget.set_percent(10, "Fetching definitions")
    widget.set_percent(100, "")
    assert widget.status_label.text() == "Fetching definitions"
    widget.set_percent(100, None)
    assert widget.status_label.text() == "Fetching definitions"


def test_set_composed_formula(widget):
    # item 2 of 4 at 50% -> (2 + 0.5) / 4 = 62%
    widget.set_composed(2, 50, 4, "Episode 3/4")
    assert widget.progress_bar.value() == 62
    assert widget.status_label.text() == "Episode 3/4"


def test_set_composed_zero_total_is_noop(widget):
    widget.set_percent(37)
    widget.set_composed(0, 50, 0, "nope")
    assert widget.progress_bar.value() == 37
    assert widget.status_label.text() != "nope"


def test_set_composed_clamps_item_pct(widget):
    widget.set_composed(0, 150, 2)
    assert widget.progress_bar.value() == 50  # (0 + 1.0) / 2


def test_show_completion_pins_100_and_freezes_stats(widget):
    widget.set_percent(40, "working")
    widget.show_completion("Complete — 87 cards created")
    assert widget.progress_bar.value() == 100
    assert widget.status_label.text() == "Complete — 87 cards created"
    # Late straggler updates must not resurrect the ETA line.
    stats_after = widget.stats_label.text()
    widget.set_percent(100, "")
    assert "ETA" not in widget.stats_label.text()
    assert widget.progress_bar.value() == 100
    del stats_after


def test_stats_line_has_no_rate_display(widget):
    widget.set_percent(50, "working")
    assert "/sec" not in widget.stats_label.text()
